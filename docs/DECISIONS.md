# Design decisions

Short records of choices that are likely to come up in review or interviews.

### 1. Go public API + Python ML service
Inference stays in Python (PyTorch, scikit-learn, LangGraph); re-implementing a fitted sklearn pipeline in Go would create serving/training skew. Go owns the boundary that benefits from it: streaming uploads with hashing, an SSRF-safe dialer, deadlines, idempotent job creation and signed media. Cost: two languages and one extra network hop (a few ms on the compose network).

### 2. Postgres for jobs, metadata, full-text and vectors (no Kafka, no vector DB)
One transactional store lets job state, manifests, index rows and the active-version pointer change atomically. `FOR UPDATE SKIP LOCKED` is a sufficient queue for one to a few workers. pgvector exact search is fast at this scale and is the baseline any approximate index (HNSW) must be benchmarked against. Revisit when a corpus is large enough that exact scans dominate latency.

### 3. Versioned index with an atomic pointer flip
Readers never see partial state, failed reindexes keep the old version, and bookmarks can pin a version. Cost: old versions consume storage until garbage-collected (not yet automated).

### 4. Attempt generations instead of trusting a single worker
Leases can expire while a slow worker is still running. Guarding every write with the attempt number makes a late, stale completion harmless. This gives at-least-once execution with idempotent effects; exactly-once is not claimed.

### 5. Fixed 20 s windows with 10 s stride
Simple and comparable across arms; the blueprint's starting point. Known cost: answers shorter than about 6 s cannot reach IoU 0.3, which evaluation reports as boundary errors. Better boundaries (sentence/slide transitions) are a separate, later experiment so their effect can be attributed.

### 6. Logistic regression as the learned scorer
Few labels, interpretable coefficients and per-result contributions, fast, and hard to overfit with regularization chosen on dev. It is a pointwise classifier used for ranking. A more complex model is justified only if dev results show underfitting.

### 7. Reciprocal-rank fusion as the fixed-fusion baseline (arm E)
Channel scores live on different scales (BM25-like ranks, cosine similarities from two different models), so rank-based fusion is the standard untrained combination. Arm F uses exactly the same candidate pool, so E vs F isolates learning.

### 8. Rule-based intent parser by default, LLM optional
The demo must work with no key and no cost. The LLM, when configured, only fills a validated schema; IDs are always resolved in code from the stored result snapshot.

### 9. Labels by content hash, splits by video group
Labels must survive re-ingestion and live in Git. Splitting by group (series or recording) prevents near-duplicate content from appearing on both sides of a split.

### 9a. AI-written labels, audited by a random human spot-check
Writing and verifying 60+ labels by hand is the slowest step. The label set is written by an AI assistant from word-level transcript timings, on-screen text and 2-second frames, and a person audits a random, type-stratified sample of 20 (fixed seed) in the Label tab: accept as is, correct, or reject. This is enforced in data: `eval_queries.author`, `spot_check`, `review_verdict`; rejected labels are kept but never used; training and evaluation refuse to run while the sample is unreviewed. Reports state the provenance and the sample's outcome, because labels written from the system's own transcripts can favor the text arms and are not independent human ground truth. Labels the author writes (`owner`) or fully reviews are reported separately.

### 10. CPU-first model choices
`faster-whisper base.en` (int8), Tesseract, MiniLM-L6 and OpenCLIP ViT-B/32 fit an 8 GB laptop. The worker unloads each model after its stage. A GPU machine can switch `asr.device` and the model size in `configs/index.yaml` (which creates a new index version).
