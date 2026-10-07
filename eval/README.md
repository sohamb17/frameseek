# Evaluation data

- `labels.jsonl` - human-written relevance labels exported from the UI (Label tab) with
  `docker compose exec worker python -m frameseek.eval.labels export`. Videos are referenced
  by content SHA-256, so labels survive re-ingestion of the same files.
- `labels.assistant.jsonl` - 79 labels written and checked by an AI assistant (Claude) from word-level
  transcript timings, on-screen text and 2-second frames (`"author": "assistant"`). 20 of them, drawn at
  random per query type, are flagged `spot_check`. Import with
  `docker compose exec worker python -m frameseek.eval.labels import --file /app/eval/labels.assistant.jsonl`
  and do the spot-check in the Label tab (**Start review**): accept as is, fix and save, or reject.
  Training and evaluation wait for the spot-check; reports state the provenance and its outcome.
- `splits.json` - the frozen train/dev/test assignment **by video group** (series or recording).
  Created once with `python -m frameseek.eval.labels freeze-splits`; do not regenerate after
  model selection has started.

- `conversation_tasks.json` - scripted multi-turn dialogues for `python -m frameseek.eval.conversation_eval`
  (development set; checks reference resolution, filters, clarification and turn safety).

Reports are written to the data volume (`/data/eval/reports/`) and shown in the Evaluation tab.
`labels.jsonl` is written by `export` after review; it keeps each label's author and review time.
