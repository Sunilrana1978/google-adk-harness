---
name: sales-analysis
description: Analyze sales numbers by region or product. Use for questions about revenue, trends, or top sellers.
tools: run_sales_query, run_python
model: gemini-2.5-flash
---

# Sales analysis skill

## Steps
1. Call `run_sales_query` to fetch rows for the region or product asked about.
2. If a calculation is needed (growth %, averages), call `run_python` with a short script.
   The script runs in a restricted subprocess: no network, short timeout, memory cap.
3. Report the number and one sentence of interpretation.

## Rules
- Show the figures you used.
- Never write files from `run_python`.
