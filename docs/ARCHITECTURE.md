# FrameSeek architecture

## Services

| Service | Language | Exposed | Owns |
|---|---|---|---|
| `api` | Go | :8080 (UI + `/api` + `/media`) | migrations, auth/owner scope, uploads, URL import, job creation, ML deadlines, signed media URLs, static UI |
| `ml` | Python (FastAPI) | private :8000 | query embeddings, retrieval, learned scorer, LangGraph conversations |
| `worker` | Python | none | ingestion stages, leases, manifests, index publication |
| `db` | Postgres 16 + pgvector | 127.0.0.1:5432 | all state (no message broker) |

Go never runs models and Python never authenticates users. The browser only talks to Go.

## Request paths

**Search**: browser → `POST /api/search` (Go validates, resolves the owner from auth, checks collection ownership) → `POST ml:/search` with a 15 s deadline → Python reads a consistent snapshot, ranks, records the search → Go adds signed media URLs → browser.

**Conversation turn**: browser → `POST /api/conversations/{id}/messages` with a client turn id → Go loads the conversation (owner check), reserves a sequence number (or replays a duplicate) → `POST ml:/conversation/turn` (30 s deadline) → LangGraph → the custom LangChain retriever calls Go's `/api/search` with the internal token and the owner Go supplied → Go → Python search. The search path never re-enters the conversation handler.

**Media**: `/media/videos/{id}/playback?exp&sig` is an HMAC-signed, expiring link; Go serves it with HTTP Range support so `<video>` seeking works.

## Data model (abridged)

```
collections(id, owner_id, name)
videos(id, owner_id, collection_id, title, content_type, content_hash, original_key, playback_key,
       duration_ms, probe, status, active_index_version)
index_versions(video_id, version, status: building|ready|failed|superseded, config_hash)
jobs(id, video_id, idempotency_key, index_version, status, stage, attempt, lease_owner, lease_expires_at, ...)
stage_manifests(video_id, index_version, stage, attempt, input_hash, config_hash, code_version,
                versions, outputs{key: sha256}, metrics)
asr_segments / frames(ts_ms, ocr_text, ocr_lines, is_transition) / frame_embeddings(vector(512))
segments(start_ms, end_ms, transcript, ocr_text, coverage..., tsv_transcript, tsv_ocr)
segment_embeddings(segment_id, kind: transcript|ocr, vector(384))
searches(snapshot, results) · feedback · bookmarks(index_version) · eval_queries/eval_answers
conversations · conversation_turns(seq, client_turn_id, response) · LangGraph checkpoint tables
model_versions(feature_schema, model_sha256, path, metrics, is_active)
```

Every searchable row carries `(video_id, index_version)`; search joins only on the active version.

## Ingestion pipeline

```
probe -> playback -> extract -> asr -> ocr -> segment -> embed -> index (validate + publish)
```

| Stage | Output | Notes |
|---|---|---|
| probe | `probe.json` | ffprobe the actual streams; reject no-video or over-limit inputs as permanent errors |
| playback | `playback.json` (+ `playback.mp4`) | reuse the original if it is browser-safe H.264/AAC MP4, else transcode; all later timestamps are on this asset's timeline |
| extract | `audio.wav`, `frames/*.jpg`, `frames.json` | 1 frame / 2 s plus scene changes; timestamps from FFmpeg `showinfo` PTS (correct for variable frame rate) |
| asr | `asr.json` | faster-whisper `base.en` int8, word timestamps, VAD; offset by the audio stream start |
| ocr | `ocr.json` | Tesseract with word boxes and confidences; skipped when a frame is nearly identical to the last OCR'd frame |
| segment | `segments.json` | 20 s windows, 10 s stride; words by midpoint; OCR lines deduplicated; coverage features |
| embed | `embeddings.npz` | MiniLM (384-d) for transcript and OCR text; OpenCLIP ViT-B/32 (512-d) for frames, streamed in batches |
| index | database rows | validate shapes and coverage, write the version, flip the pointer, mark the job succeeded, all in one transaction |

### Job state machine

```
queued --claim--> running --publish--> succeeded
   ^                 |  \--permanent error--> failed (previous version stays active)
   |                 |--transient error, attempts left--> queued (backoff 5 s, 10 s, ...)
   |                 '--lease expires (worker died)--> reclaimable by any worker (attempt + 1)
```

- **Claim** increments `attempt`. Heartbeats extend the lease only while `attempt` still matches; zero rows updated means the worker lost ownership and stops at the next check.
- **Guarded writes**: manifest upserts, progress updates, failure handling and publish all check the attempt. The publish transaction locks the job row with `FOR UPDATE` first.
- **Resume**: a stage is skipped when its manifest's config hash and code version match and every output's checksum verifies.
- **Idempotency key** = `sha256(content_hash + ":" + sha256(configs/index.yaml bytes))`. Go and Python hash the same raw bytes, so there is no canonicalization mismatch. A partial unique index on live jobs prevents duplicates even under concurrent submissions.
- Execution is **at-least-once**; effects are idempotent (outputs overwrite atomically, the version's rows are deleted and rewritten inside the publish transaction). Exactly-once is not claimed.

## Retrieval

For a query `q`:

1. `REPEATABLE READ, READ ONLY` transaction: snapshot `(video_id, active_index_version)` pairs in scope.
2. `qv = MiniLM(q)`, `cv = CLIP_text(q)` (cached per process).
3. Channels (top 30 each): `ts_rank_cd` over an OR-ed `plainto_tsquery` on transcript and OCR; cosine distance on transcript and OCR vectors; CLIP cosine over the top 120 frames, max-pooled into the windows that contain them.
4. Candidate rows: all five raw scores for each candidate (lateral subqueries compute the best frame and mean frame similarity inside the window).
5. Features (`ml/frameseek/retrieval/features.py`): raw scores, scores minus the query's best (channel scales are not comparable), reciprocal channel ranks, channel count, coverage, OCR stability, duration, missing-modality flags, query length.
6. Scoring: arm E `Σ w_c / (60 + rank_c)`; arm F `StandardScaler → LogisticRegression.decision_function`.
7. Temporal NMS (IoU ≥ 0.3 within a video), top K, evidence assembly (`ts_headline` snippets, best frame, per-modality percentile bars, feature contributions for arm F).

Exact (sequential) vector search is used on purpose: at demo scale it is fast (tens of ms) and gives ground truth for a later HNSW benchmark.

## Conversation graph

State per thread (Postgres checkpoint): owner, collection, latest turn `seq`, bounded messages, current topic, filters, the ordered snapshot of the last displayed results, the selected result, and the last search id.

- `interpret`: LLM structured output (optional) or rules → `Intent` (validated by pydantic).
- `resolve`: ordinals index into the stored snapshot; "this video" uses the selected result or the only video in the list; otherwise a clarification plan with options.
- `clarify`: `interrupt()`; the node restarts on resume, so everything before `interrupt()` is side-effect free.
- `retrieve`: `FrameSeekRetriever` (LangChain `BaseRetriever`) → Go `/api/search`.
- `expand`: widen the selected result's playback window within the video bounds (not a new search, and not a localization improvement).
- `validate`: only results present in what was retrieved or displayed may be cited.
- `respond`: short templated explanation, results, interpretation chips.

Turn safety: Go dedupes by `client_turn_id`; Python rejects a turn whose `seq` is not newer than the checkpoint's; a per-thread lock serializes turns in-process; a collection switch clears references.

## Recorded demo build

`VITE_DEMO=1` builds the same UI against static files: `web/public/demo/data.json` (videos, example queries, recorded conversations), one JSON per query and arm, and 240 px thumbnails, all written by `scripts/export_demo.py` from real pipeline output. Playback uses the original public recordings with an `offset_ms` per excerpt, so no media is hosted in the repo. Upload, labeling, evaluation, feedback and bookmarks are hidden in this build.

## Security notes

- URL import: `https` only, no credentials in URLs, every DNS answer must be a public unicast address (loopback, RFC 1918, link-local/metadata, CGNAT, ULA, NAT64 and documentation ranges are blocked), the connection goes to the checked IP, redirects are re-checked, size and time are bounded, HTML responses are rejected.
- Uploads: streamed to disk with a byte limit, container identified by magic bytes, never by extension.
- Media: HMAC-signed expiring URLs; storage keys cannot escape the data root.
- Scope: owner comes from authentication; the conversation's collection comes from the database; model output cannot widen scope; retrieved text is treated as data.
- Not done (demo trade-offs): media parsing is not sandboxed beyond the container, there is no rate limiting, and `AUTH_MODE=none` is for local use only.

## Measured in development (not a benchmark)

On a 2-vCPU, 8 GB cloud sandbox, CPU only, 6 videos / 36 minutes:

- worker peak RSS about 1.4 GB with `FRAMESEEK_LOW_MEMORY=true`
- warm search latency about 0.1-0.5 s end to end, dominated by query encoding; candidate generation plus features about 20-40 ms at this corpus size
- per-stage wall times are recorded in each video's manifests (Library → Details) and summarized per source-hour in evaluation reports

These are development observations, not claims about other hardware or corpus sizes.
