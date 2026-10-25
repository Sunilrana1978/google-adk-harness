---
name: refund-policy
description: Decide whether an order is eligible for a refund and explain why. Use for refund, return, or money-back questions.
model: gemini-2.5-flash
tools: lookup_order, check_refund_eligibility
---

# Refund policy skill

Written by the Customer Care team. No Python needed to change this.

## Steps
1. Call `lookup_order` with the order id the customer gave you.
2. Call `check_refund_eligibility` with the same order id.
3. Reply with: decision (eligible / not eligible), the reason, and the next step.

## Rules
- Never promise a refund the eligibility tool did not approve.
- If the order id is missing, ask for it. Do not guess.
- Keep the answer under 5 sentences.
