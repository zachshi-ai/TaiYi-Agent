# TaiYi Controlled Cross-Harness Smoke

- Measurement scope: `controlled_transport_tool_conformance`
- Model endpoint identity: `taiyi-controlled-model-v1`
- Comparable cells: 2 / 4
- Comparability signatures match: YES
- Comparable cells passed: YES
- False completions: 0
- Ranking eligible: NO

| Harness | Status | Comparable | Task passed | Budget passed | Model requests | Tool calls | Blocker |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| taiyi | MEASURED | yes | yes | yes | 2 | 1 | - |
| pi | MEASURED | yes | yes | yes | 2 | 1 | - |
| openclaw | NOT_COMPARABLE | no | no | no | 0 | 0 | installed OpenClaw release lacks the isolated agent exec batch interface |
| zcode | NOT_COMPARABLE | no | no | no | 0 | 0 | ZCode desktop is installed but has no documented non-interactive batch interface |

## Claim boundary

- This compares adapter transport, isolated tool execution, and completion truth.
- The controlled endpoint is not a real intelligence or coding-quality model.
- Real-provider ranking requires a separately frozen provider/model revision and budget.
