# Evaluation data

- `labels.jsonl` - human-written relevance labels exported from the UI (Label tab) with
  `docker compose exec worker python -m frameseek.eval.labels export`. Videos are referenced
  by content SHA-256, so labels survive re-ingestion of the same files.
- `splits.json` - the frozen train/dev/test assignment **by video group** (series or recording).
  Created once with `python -m frameseek.eval.labels freeze-splits`; do not regenerate after
  model selection has started.

Reports are written to the data volume (`/data/eval/reports/`) and shown in the Evaluation tab.
No labels are included yet: they must be written by a person who watched the videos.
