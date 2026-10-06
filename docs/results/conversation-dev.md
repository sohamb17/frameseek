# Conversational search evaluation (development set)

> **Development set, not a held-out evaluation.** These 14 scripted dialogues were written by the developer alongside the rule-based intent parser, so a 100% pass rate shows the behaviour works as designed on these phrasings, not how well it generalizes. A held-out set written by someone else (planned) is needed for an unbiased estimate. Measured in a 2-vCPU CPU-only dev sandbox on the 6-video sample library; the p95 retrieve latency includes the first, cold query.

2026-10-06T04:52:09+00:00 · parser: rules · 14 scripted tasks, 35 turns · 14/14 tasks passed every check

| Check | Passed | Rate |
|---|---|---|
| clarification_options | 2/2 | 100% |
| filter_content_type | 4/4 | 100% |
| filter_excludes_video | 1/1 | 100% |
| filter_within_video | 2/2 | 100% |
| min_results | 14/14 | 100% |
| new_topic_clears_video_filters | 2/2 | 100% |
| no_invented_results | 4/4 | 100% |
| precondition_multi_video | 2/2 | 100% |
| reference_resolution | 5/5 | 100% |
| status_clarify | 3/3 | 100% |
| status_ok | 32/32 | 100% |
| topic_kept | 8/8 | 100% |
| type_filter_cleared | 3/3 | 100% |

Clarification asked when expected: 100% · unnecessary clarification: 0%

| Safety property | Result |
|---|---|
| duplicate_turn_replayed | pass |
| duplicate_turn_not_stored_twice | pass |
| stale_turn_rejected | pass |
| other_owner_blocked | pass |

**Baselines.** Stateless search of the follow-up text alone: topic_kept 0% (n=7), filter_content_type 0% (n=4), reference_resolution 0% (n=4). Conversation results identical to an explicit-filter search with the same query and filters: 100% (n=23).

| Turn type | n | p50 ms | p95 ms |
|---|---|---|---|
| retrieve | 23 | 152.7 | 3361.8 |
| expand | 5 | 28.3 | 33.0 |
| clarify | 3 | 25.1 | 25.5 |
| no_answer | 3 | 17.6 | 22.8 |
| reset | 1 | 21.7 | 21.7 |

These checks measure resolution and state handling, not whether the retrieved moments are relevant;
relevance is measured by the labeled retrieval evaluation. Failures are listed in the JSON report.
