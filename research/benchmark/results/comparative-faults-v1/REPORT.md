# TaiYi Controlled Cross-Harness Fault Matrix

- Cases: 2
- Comparable cells: 6 / 6
- Attributed failures: 6
- Safe failures: 6
- False completions: 0
- Complete matrix: YES
- Ranking eligible: NO

| Case | Harness | Status | Comparable | First request (s) | Failure phase | Failure kind | Safe | Observed error / blocker |
| --- | --- | --- | ---: | ---: | --- | --- | ---: | --- |
| model_first_token_timeout | taiyi | MEASURED | yes | 0.207 | LLM_FIRST_TOKEN | LLM_FIRST_TOKEN_TIMEOUT | yes | - |
| model_first_token_timeout | pi | MEASURED | yes | 0.456 | LLM_FIRST_TOKEN | LLM_FIRST_TOKEN_TIMEOUT | yes | outer benchmark timeout |
| model_first_token_timeout | openclaw | MEASURED | yes | 7.818 | LLM_FIRST_TOKEN | LLM_FIRST_TOKEN_TIMEOUT | yes | outer benchmark timeout |
| model_first_token_timeout | zcode | NOT_COMPARABLE | no | - | - | - | no | ZCode desktop is installed but has no documented non-interactive batch interface |
| model_stream_idle_timeout | taiyi | MEASURED | yes | 0.271 | LLM_STREAM_IDLE | LLM_STREAM_IDLE_TIMEOUT | yes | - |
| model_stream_idle_timeout | pi | MEASURED | yes | 0.427 | LLM_STREAM_IDLE | LLM_STREAM_IDLE_TIMEOUT | yes | outer benchmark timeout |
| model_stream_idle_timeout | openclaw | MEASURED | yes | 7.884 | LLM_STREAM_IDLE | LLM_STREAM_IDLE_TIMEOUT | yes | outer benchmark timeout |
| model_stream_idle_timeout | zcode | NOT_COMPARABLE | no | - | - | - | no | ZCode desktop is installed but has no documented non-interactive batch interface |

## Claim boundary

- This matrix measures failure attribution and safe termination, not model quality.
- The controlled stalls are deterministic substitutes for provider failures.
- Real large-repository ranking still requires one frozen provider/model and repository image.
