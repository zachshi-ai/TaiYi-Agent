# TaiYi Harness Protocol Benchmark

- Measurement scope: `harness_protocol_conformance`
- Provider: `scripted-fault-provider/v1`
- Measured runs: 18
- Protocol conformance: PASS
- False completions: 0
- Duplicate effects: 0

| Mode | Task pass | Protocol pass | Fault recovery | Human handoff | LLM calls | Connector attempts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| quality | 83.3% | 100.0% | 75.0% | 16.7% | 13 | 8 |
| balanced | 66.7% | 100.0% | 50.0% | 16.7% | 12 | 7 |
| efficiency | 50.0% | 100.0% | 25.0% | 16.7% | 10 | 6 |

## Claim boundary

- This report measures TaiYi protocol behavior with a deterministic scripted provider.
- It does not establish model quality or a cross-harness ranking.
- Only external cells with the same comparability signature may be ranked together.

## External harness capability cells

| Harness | Status | Version | Reason |
| --- | --- | --- | --- |
| pi | UNAVAILABLE | - | pi CLI is not installed |
| openclaw | NOT_COMPARABLE | OpenClaw 2026.7.1-2 (0790d9f) | installed profile is host-running or isolation could not be proven; a dedicated benchmark profile and common model route are required |
| zcode | UNAVAILABLE | - | desktop app is installed but no documented non-interactive CLI/adapter is available |
