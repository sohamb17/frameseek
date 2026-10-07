# Retrieval evaluation (October 7, 2026)

Corpus: 6 videos, 0.6 h, index config `c4a2af3963f1`. Learned scorer `3aa39153bb61` (logistic regression, 25 features, C = 0.01).
Success = a top-K result in the correct video with temporal IoU ≥ 0.3 against a labeled interval.

## Labels

79 queries in `eval/labels.assistant.jsonl` (32 speech, 17 on-screen text, 13 mixed, 11 visual, 6 no-answer), written and checked by an AI assistant (Claude) from word-level transcript timings, on-screen text and 2-second frames. A random sample of 20, stratified by query type (seed 20261006), was spot-checked by the author in the Label tab: **20 accepted as is, 0 corrected, 0 rejected**. The labels are not independent human ground truth: queries were written while reading the system's own transcripts, which can favor the text arms.

Splits are frozen by video group (`eval/splits.json`, created before any labels were loaded): train = TiDB, rash, OwnTech; dev = Distributed Databases; test = both NASA STEMonstrations videos.

## Test split (held out; evaluated once)

27 answerable queries from one video series. Full report: [retrieval-test.md](retrieval-test.md).

| Arm | System | success@1 | success@5 | success@10 | MRR | success@5 at IoU 0.5 |
|---|---|---|---|---|---|---|
| A | transcript keywords | 0.48 | 0.63 | 0.70 | 0.56 | 0.37 |
| B | transcript semantic | 0.56 | 0.67 | 0.67 | 0.60 | 0.22 |
| C | transcript + OCR, fixed fusion | 0.33 | 0.56 | 0.70 | 0.42 | 0.19 |
| D | visual only | 0.22 | 0.48 | 0.52 | 0.32 | 0.22 |
| E | all modalities, fixed fusion | 0.26 | 0.56 | 0.63 | 0.39 | 0.19 |
| F | all modalities, learned | 0.48 | 0.67 | 0.70 | 0.57 | 0.26 |

By query type, success@5 for F (B): speech 8/9 (8/9), mixed 4/5 (5/5), on-screen text 3/7 (2/7), visual 3/6 (3/6).

Failures of F at success@5 (9): 6 boundary errors, all on answers 3.5-5.5 s long (the overlapping window is in the candidate pool, and for 4 of them it is the top result, but short on-screen captions and visual shots cannot be matched: a 20 s window cannot reach IoU 0.3 with an answer shorter than 6 s), and 3 ranking errors. The short labels follow the labeling rule (minimum useful interval), so this is a real localization limit of fixed 20 s windows, not a labeling mistake; the protocol was not changed after seeing the test results.

## Cross-validation (train + dev, leave one video group out, C = 1.0)

46 answerable queries, 4 folds. 95% intervals by bootstrap over video groups.

| Arm | success@1 | success@5 | MRR | success@5 95% CI |
|---|---|---|---|---|
| E fixed fusion | 0.41 | 0.76 | 0.58 | 0.67-0.83 |
| F learned | 0.54 | 0.80 | 0.67 | 0.67-0.88 |

## Dev split

9 answerable queries + 2 no-answer, one recording ([retrieval-dev.md](retrieval-dev.md)). The dev split chose `C` and the abstention threshold, so F's dev score (success@5 0.89) is optimistic. F abstained correctly on both no-answer queries; arms A-E have no abstention.

## Reading these numbers

- The learned scorer clearly beats fixed rank fusion (E) at top-1 on held-out videos and in cross-validation.
- It does not beat transcript semantic search (B) on this corpus. Most answers are narrated, so the transcript channel carries most of the signal.
- Test differences of one or two queries (0.04-0.07) are within noise.
- Latency (dev, warm, CPU laptop): p50 about 0.35 s end to end, of which about 0.25 s is query embedding and about 0.06 s retrieval and scoring.
