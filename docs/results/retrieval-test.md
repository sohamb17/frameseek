# FrameSeek evaluation (test split)

Created 2026-10-07T03:22:11+00:00. 27 labeled queries. Corpus: 6 videos, 0.598 h. Index config c4a2af3963f1. Model: 3aa39153bb61.

Label provenance: 27 written and checked by an AI assistant (Claude) from transcripts, OCR and frames; a random sample of 6 was spot-checked by the author: 6 accepted as is, 0 corrected, 0 rejected.

| Arm | System | success@1 | success@5 | success@10 | MRR | cand. recall | n |
|---|---|---|---|---|---|---|---|
| A | Transcript keyword search | 0.4815 | 0.6296 | 0.7037 | 0.5643 | 0.7037 | 27 |
| B | Transcript semantic search | 0.5556 | 0.6667 | 0.6667 | 0.6049 | 0.7778 | 27 |
| C | Transcript + on-screen text (fixed fusion) | 0.3333 | 0.5556 | 0.7037 | 0.4227 | 0.7778 | 27 |
| D | Visual only (CLIP) | 0.2222 | 0.4815 | 0.5185 | 0.3195 | 0.7407 | 27 |
| E | All modalities, fixed rank fusion | 0.2593 | 0.5556 | 0.6296 | 0.3852 | 0.7778 | 27 |
| F | All modalities, learned scorer | 0.4815 | 0.6667 | 0.7037 | 0.5725 | 0.7778 | 27 |

Success uses temporal IoU >= 0.3 with a labeled interval in the correct video.
Small pilot sets give wide intervals; see the grouped bootstrap CI in the JSON report.

## Failures (primary arm, success@5)

- [ranking_error] (mixed) three types of friction
- [ranking_error] (visual) astronaut floating a big white bag through the station
- [boundary_error] (visual) astronaut on a spacewalk outside the station
- [boundary_error] (ocr) sharpening scissors as an example of kinetic friction
- [boundary_error] (ocr) sanding wood as kinetic friction
- [boundary_error] (ocr) ladder leaning on a wall as static friction
- [ranking_error] (speech) a screw is a rod with an inclined plane spiraling around it
- [boundary_error] (ocr) car parked on a hill as static friction
- [boundary_error] (visual) sparks flying off a grinding wheel
