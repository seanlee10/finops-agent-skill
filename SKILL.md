---
name: finops-report
description: LLM spend chargeback, budget burn, unit economics (cost per session / per completed session) and Cost Agent cross-reference from Arize cost aggregates. Use for questions about who spent what, budget status, cost per conversation, or spend by team, cost center, project, space or segment.
---

# FinOps report

Every figure in your answer comes from the script's output. Do not add, average, extrapolate or round figures yourself, and do not estimate numbers the script did not print.

## Run

```bash
python3 scripts/finops.py --config <config path from the task prompt> --format md
```

- Default window: month to date in the config's timezone. For another window add `--start <ISO> --end <ISO>`.
- For follow-up questions use `--format json` and quote the relevant fields.
- The script reads Arize GraphQL at `https://graphql.sandboxcontroller/graphql`, the documented sandbox virtual host for `app.arize.com/graphql`. The proxy injects the credential; send none.

## Scheduled report (automation runs)

When the task asks for the periodic FinOps report:

1. Run `python3 scripts/finops.py --config <config> --format investigation > /tmp/finops-investigation.md`.
2. Call `emit_investigation` (from the `arize-artifacts` MCP server) with `body_md` set to the file's exact contents. Do not edit, shorten or reformat it; it already has the investigation-file sections, the budget issue cards and the full report.
3. Your final message is the same file's contents, verbatim.

## Answering


1. Lead with the answer to the question, quoting the script's figures.
2. Include the report sections the question needs, in this order: chargeback, budgets, unit economics, Cost Agent issues.
3. Always include the **Data quality** section as the footer.
4. If the output starts with **Totals do not reconcile**, say so first and do not present the figures as final.
5. If unit economics are labelled `estimate (n of N sessions evaluated)`, keep that label.
6. Never quote span inputs, outputs or message content. Report numbers, labels and IDs only.
7. For savings opportunities, point to the listed Cost Agent issues; do not do your own optimization analysis.

## Errors

- `config error` (exit 2): report the message; do not edit the config.
- `Arize query failed` (exit 3): report the message. An `Unauthorized` error means the agent config lacks the read-only Arize integration that grants dashboard read.
