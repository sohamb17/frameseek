# FrameSeek evaluation (dev split)

Created 2026-10-07T03:18:52+00:00. 11 labeled queries. Corpus: 6 videos, 0.598 h. Index config c4a2af3963f1. Model: 3aa39153bb61.

Label provenance: 11 written and checked by an AI assistant (Claude) from transcripts, OCR and frames; a random sample of 4 was spot-checked by the author: 4 accepted as is, 0 corrected, 0 rejected.

| Arm | System | success@1 | success@5 | success@10 | MRR | cand. recall | n |
|---|---|---|---|---|---|---|---|
| A | Transcript keyword search | 0.2222 | 0.6667 | 0.6667 | 0.4167 | 0.7778 | 9 |
| B | Transcript semantic search | 0.6667 | 0.8889 | 1.0 | 0.7611 | 1.0 | 9 |
| C | Transcript + on-screen text (fixed fusion) | 0.5556 | 0.8889 | 0.8889 | 0.6852 | 1.0 | 9 |
| D | Visual only (CLIP) | 0.2222 | 0.4444 | 0.6667 | 0.3317 | 0.6667 | 9 |
| E | All modalities, fixed rank fusion | 0.4444 | 0.6667 | 0.8889 | 0.5926 | 1.0 | 9 |
| F | All modalities, learned scorer | 0.5556 | 0.8889 | 0.8889 | 0.6759 | 1.0 | 9 |

Success uses temporal IoU >= 0.3 with a labeled interval in the correct video.
Small pilot sets give wide intervals; see the grouped bootstrap CI in the JSON report.

## Failures (primary arm, success@5)

- [ranking_error] (mixed) biggest aws instance you can rent, how many cores and how much memory
