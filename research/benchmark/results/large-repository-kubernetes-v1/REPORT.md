# TaiYi large-repository combined resilience baseline

- Source: `pinned_git_checkout`
- Git HEAD: `52ba90138eb40cab0987dac73e05c838149bdd1c`
- Snapshot: `sha256:0c8150939370149dc60c8c15cb4a03dc09380c9913a42c551c2544f2cc6f7d72`
- Indexed files/chunks: 25683 / 59825
- Protocol passes: 12/12
- False completions: 0
- Duplicate effects: 0

| Case | Quality | Balanced | Efficiency |
| --- | --- | --- | --- |
| index_process_restart | PASS | PASS | PASS |
| frozen_snapshot_first_token_timeout | PASS | PASS | PASS |
| large_tool_output_context_overflow | PASS | PASS | PASS |
| model_wait_process_restart | PASS | PASS | PASS |

External harnesses remain NOT_COMPARABLE until they expose the same pinned source, model, context, fault, checkpoint, and effect receipt boundary.
