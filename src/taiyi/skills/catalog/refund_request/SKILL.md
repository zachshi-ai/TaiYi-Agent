---
name: refund_request
category: managed
risk: high
applicability: Customer service refund scenarios
triggers: [退款, refund]
scenario: customer_service.refund
---
# Refund Request

## Steps
1. Parse the refund amount from the request
2. `tool:refund` — process the refund (amount > 100 is held for review)
3. Record the outcome for end-to-end traceability

## Red lines
- Refunds over 100 require human approval.
- No direct ledger edits.
- Keep tone formal; filter sensitive content.
