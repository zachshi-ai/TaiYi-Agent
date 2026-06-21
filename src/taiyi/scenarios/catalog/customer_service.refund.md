---
name: customer_service.refund
description: Customer service refund handling
triggers: [退款, refund, 退货, 投诉]
---
# Scenario: customer_service.refund (refund handling)

## Constraints
1. Refunds over 100 require human approval.
2. Tone must stay formal; sensitive content is filtered.
3. Every refund is traceable end-to-end (linked to retention strategy).

## Tool permissions
- ✅ tool:refund (amount ≤ 100 auto; > 100 needs review)
- ⚠️ outbound customer messaging — peer-reviewed
- ❌ direct ledger edits

## Credentials
- Scoped refund-service token, isolated per the credential policy.
