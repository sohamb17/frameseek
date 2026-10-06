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
- **Training data**: human-written queries with labeled answer intervals from the `train` split only. A candidate window is positive if it has temporal IoU ≥ 0.3 with an answer or covers at least half of it (evaluation keeps the stricter IoU rule).
- **Weighting**: each query's candidates sum to weight 1, then classes are balanced.
- **Selection**: `C ∈ {0.01, 0.1, 1, 10}` by dev success@5; the abstention threshold is chosen on dev only.
- **Output**: a relevance score for ranking. It is **not** a calibrated probability.
- **Status**: no model has been trained on real labels yet.

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

## Known limitations

- English only; Whisper base can mis-hear domain terms (raw ASR is preserved, not corrected).
- OCR is noisy on photographed scenes and small terminal fonts; the FOSDEM overlay text appears in every frame.
- Isolated frames do not represent actions ("the step where the cable is connected") and short events can fall between 2 s samples.
- 20 s windows limit localization precision.
