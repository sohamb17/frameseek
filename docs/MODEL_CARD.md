# Models and data card

## Pretrained models (inference only, nothing here is fine-tuned)

| Role | Model | Pinned revision | License | Runtime |
|---|---|---|---|---|
| Speech recognition | `Systran/faster-whisper-base.en` (CTranslate2 Whisper base.en) | `3d3d5dee26484f91867d81cb899cfcf72b96be6c` | MIT | CPU int8, word timestamps, VAD |
| On-screen text | Tesseract OCR (Debian package, English) | recorded per manifest | Apache-2.0 | CPU, `--psm 3`, words with conf ≥ 70 |
| Text encoder | `sentence-transformers/all-MiniLM-L6-v2` (384-d) | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` | Apache-2.0 | CPU, normalized |
| Image/text encoder | OpenCLIP ViT-B-32 `laion2b_s34b_b79k` (512-d) | `1a25a446712ba5ee05982a381eed697ef9b435cf` | MIT | CPU, normalized |

Revisions are pinned in `configs/index.yaml` and every stage manifest records the model, revision, FFmpeg and Tesseract versions actually used.

## Learned relevance scorer

- **Type**: `StandardScaler` + `LogisticRegression` over the 25 features in `ml/frameseek/retrieval/features.py` (schema version 1).
- **Training data**: queries with labeled answer intervals from the `train` split only, written by an AI assistant and audited by a random human spot-check, or written by the author (see Label provenance). Rejected labels are never used. A candidate window is positive if it has temporal IoU ≥ 0.3 with an answer or covers at least half of it (evaluation keeps the stricter IoU rule).
- **Weighting**: each query's candidates sum to weight 1, then classes are balanced.
- **Selection**: `C ∈ {0.01, 0.1, 1, 10}` by dev success@5; the abstention threshold is chosen on dev only.
- **Output**: a relevance score for ranking. It is **not** a calibrated probability.
- **Current model**: `3aa39153bb61`, trained on 37 answerable train queries (TiDB, rash and OwnTech recordings); `C = 0.01` selected on dev (9 queries, a tie with 0.1 broken toward stronger regularization); abstention threshold 4.22 chosen on dev. Largest standardized coefficients: transcript semantic score relative to the query's best (+0.59), OCR coverage (-0.37), transcript lexical relative score (+0.36), number of channels that found the window (+0.34).
- **Held-out test result** (27 queries, NASA videos): success@5 0.67 vs 0.56 for fixed fusion (E) and 0.67 for transcript semantic search (B); success@1 0.48 vs 0.26 (E) and 0.56 (B). See [docs/results/retrieval.md](results/retrieval.md).

## Sample corpus (demo library)

| Slug | Type | Duration | Source | License |
|---|---|---|---|---|
| tidb-replication | talk | 12:31 | FOSDEM 2025 | CC BY 2.0 BE |
| distributed-databases | talk | 8:00 (excerpt) | FOSDEM 2025 | CC BY 2.0 BE |
| rash-shell-demo | screencast | 4:25 (excerpt, AV1/Opus WebM) | FOSDEM 2025 | CC BY 2.0 BE |
| owntech-hardware-demo | demo | 2:10 (excerpt) | FOSDEM 2025 | CC BY 2.0 BE |
| nasa-simple-machines | demo | 3:50 | NASA JSC | NASA media, public domain in the US |
| nasa-friction | demo | 4:57 | NASA JSC | NASA media, public domain in the US |

Exact URLs, excerpt windows, attribution and SHA-256 checksums are produced by `scripts/fetch_samples.py` into `manifest.json`. Media files are never committed.

This is a feasibility corpus, not an evaluation dataset: six recordings from three sources cannot support broad conclusions. Slide-heavy talks dominate; only two recordings show physical demonstrations.

## Labeling protocol

1. Watch the recording first; write the query a real user would type **before** searching for it.
2. Mark the minimum useful interval (aim for 5-30 s); add every valid alternative answer.
3. Assign one type: speech, ocr, visual, mixed, no_answer.
4. Note ambiguity in the notes field. If no second reviewer adjudicates a subset, report single-annotator bias.
5. Freeze splits by video group before any model selection; never tune on the test split.

## Label provenance

Every label records its author: `owner` (written by a person in the Label tab), `assistant` (written and checked by an AI assistant) or `assistant-draft` (an AI proposal that counts only after a person reviews it).

The current set, `eval/labels.assistant.jsonl` (79 queries: 32 speech, 17 on-screen text, 13 mixed, 11 visual, 6 no-answer), was written by Claude in two passes: a first draft from transcripts and 8-second keyframes, then a strict pass that set speech boundaries from word-level ASR timings and checked every visual and on-screen-text boundary against the 2-second frames. Claude read transcripts and looked at frames; it did not listen to the audio. Its timelines were verified against the author's indexed copies by matching transcript words (median offset 0 s on all six videos).

- A random sample of 20, stratified by query type (seed 20261006), is flagged `spot_check`. The author watches each one in the Label tab and records a verdict: accepted as is (clip answers the query, edges within about 2 s), corrected, or rejected. Training and evaluation refuse to run until all 20 are done.
- Rejected labels are kept, so they count against the reported agreement, and are never used.
- The labels are not independent human ground truth. Queries were written while reading the pipeline's own transcripts, so their wording can lean toward words the ASR produced, which can favor the lexical arms (A, C). The visual and on-screen-text labels are less exposed to this.
- Every report prints the provenance and the spot-check outcome. Results are described as "on AI-written labels audited by a random author spot-check (X/20 accepted as is)", never as human-labeled.

## Known limitations

- English only; Whisper base can mis-hear domain terms (raw ASR is preserved, not corrected).
- OCR is noisy on photographed scenes and small terminal fonts; the FOSDEM overlay text appears in every frame.
- Isolated frames do not represent actions ("the step where the cable is connected") and short events can fall between 2 s samples.
- 20 s windows limit localization precision.
