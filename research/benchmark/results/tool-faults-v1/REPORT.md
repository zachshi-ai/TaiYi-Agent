# TaiYi Production Tool-Process Fault Matrix

- Cases: 5
- Runs: 15
- Protocol passed: 15 / 15
- False completions: 0
- Duplicate effects: 0
- Ranking eligible: NO

| Case | Mode | State | Failure | Evidence | Protocol | Duration (s) |
| --- | --- | --- | --- | ---: | ---: | ---: |
| hard_timeout_sigterm_resistant_tree | quality | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 1.464 |
| hard_timeout_sigterm_resistant_tree | balanced | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 1.460 |
| hard_timeout_sigterm_resistant_tree | efficiency | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 1.463 |
| idle_timeout_silent_process | quality | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 0.411 |
| idle_timeout_silent_process | balanced | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 0.449 |
| idle_timeout_silent_process | efficiency | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 0.400 |
| stdout_stderr_flood | quality | COMPLETED | - | yes | yes | 0.254 |
| stdout_stderr_flood | balanced | COMPLETED | - | yes | yes | 0.230 |
| stdout_stderr_flood | efficiency | COMPLETED | - | yes | yes | 0.226 |
| lingering_descendant | quality | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 1.321 |
| lingering_descendant | balanced | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 1.358 |
| lingering_descendant | efficiency | NEEDS_INPUT | EFFECT_OUTCOME_UNKNOWN | yes | yes | 1.358 |
| gateway_restart_reattach_once | quality | COMPLETED | - | yes | yes | 0.443 |
| gateway_restart_reattach_once | balanced | COMPLETED | - | yes | yes | 0.396 |
| gateway_restart_reattach_once | efficiency | COMPLETED | - | yes | yes | 0.393 |

## External comparison boundary

- Status: `NOT_COMPARABLE`
- Pi, OpenClaw, and ZCode do not yet expose one frozen controlled-tool interface proving identical process-tree, signal, output, and restart semantics.
