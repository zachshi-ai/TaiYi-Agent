---
name: refund_request
category: managed
risk: high
applicability: 客服退款场景
---

# 退款请求处理技能

## 触发条件
- 用户提到 "退款 / refund"
- 场景为 `customer_service.refund`

## 执行步骤
1. 校验金额
2. 触发场景约束:金额 > 100 → 人审
3. 退款执行

## 注意事项(红线)
- 金额 > 100 必须人审
- 不可绕过场景约束
- 退款必须留存凭证
