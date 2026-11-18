# finops-agent-skill

A FinOps skill for Arize AX managed agents. It totals the LLM cost recorded in Arize by your organisation's structure (team, cost center, project, space and so on) to produce chargeback reports. It also shows budget burn, cost per session and per completed session, and open Cost Agent issues.

Every figure is computed by the scripts from Arize aggregates. The agent only quotes the script output and never calculates or estimates figures itself.

This is sample code, not an officially supported Arize product.

## Contents

| Path | Contents |
| --- | --- |
| `SKILL.md` | Instructions the agent follows (how to run, answer rules, error handling) |
| `agent/system_prompt.md` | Managed agent system prompt |
| `finops/` | Report engine (Python standard library only) |
| `scripts/finops.py` | Report CLI |
| `scripts/setup_widgets.py` | One-time setup that creates the pivot widgets the report reads |
| `scripts/setup_charts.py` | Optional script that creates a daily cost chart dashboard |
| `scripts/link_arize_key.py` | Registers a read-only Arize API key as a managed agent sandbox integration |
| `examples/` | Example configurations, org charts and budget CSVs |
| `synthetic/` | Synthetic trace generator for testing |

## Prerequisites

- Python 3.10 or later (no external packages)
- Cost recorded on your traces: `llm.cost.total` and `llm.token_count.total`, plus `session.id` for per-session figures and `user.id` for per-user chargeback
- An Arize API key that can read the projects

## Quick start

### 1. Write a configuration

Copy `examples/acme.json` and edit it:

- `projects`: the Arize projects to include (`id`, `name`, `space_id`, `space_name`).
- `dimensions`: the rollup hierarchy. The first level is one of `attribute:<key>` (for example `attribute:user.id`), `space` or `project`. Higher levels map upwards through a CSV (for example user → team → cost_center).
- `budgets`: a budget CSV with the level being budgeted, `month` and `budget_usd` columns (for example `team,month,budget_usd`).
- `tz_offset_hours`: the time zone month boundaries are computed in (8 for Singapore).

### 2. Create the pivot widgets (once)

The report reads exact aggregates from pivot widgets on a dashboard. This script creates them and writes their IDs into the configuration.

```bash
export ARIZE_API_KEY=<your-arize-api-key>
python3 scripts/setup_widgets.py --config my-config.json --dry-run   # show what would be created
python3 scripts/setup_widgets.py --config my-config.json
```

### 3. Run the report

```bash
python3 scripts/finops.py --config my-config.json --format md
python3 scripts/finops.py --config my-config.json --start 2026-11-01T00:00+08:00 --end 2026-11-15T00:00+08:00
```

- The default window is month to date in the configuration's time zone.
- `--format json` is for follow-up questions, and `--format investigation` produces the investigation file with issue cards used by scheduled runs.
- For self-hosted Arize or another region, set the GraphQL endpoint with `FINOPS_GRAPHQL_URL` (for example `https://<your-arize-host>/graphql`).

## Evaluation cost by space

`examples/eval-cost.json` reports what LLM-as-a-judge evaluations cost, by space, team, cost center, evaluation task and judge model. It reads each space's **Eval Traces** project, where every judge call is recorded with its cost, model and task when eval traces are on for the task.

1. Add one entry per space to `projects`, using that space's **Eval Traces** project ID and the space name.
2. Map space names to teams and cost centers in `examples/eval-cost/teams.csv`, and set monthly budgets per team in `examples/eval-cost/budgets.csv`.
3. Run `setup_widgets.py` and then `finops.py` as above.

Notes:

- Only evaluations run with eval traces on are counted. Judge calls made with eval traces off are billed by the provider but leave no cost in Arize.
- Judge calls are not grouped into sessions, so ignore the unit economics section for this configuration.
- Costs come from the model prices Arize applies to each span. If a judge model shows no spend, check that Arize has a price configured for it, which matters most for models served through a custom endpoint.

## Deploy as a managed agent

1. Create a custom managed agent in Arize and connect this repository as a skill. Use `agent/system_prompt.md` as the system prompt.
2. Register a **read-only** API key with dashboard read access as a sandbox integration, once. The script takes the key from the `FINOPS_READONLY_KEY` environment variable or a hidden prompt and never prints it.

   ```bash
   python3 scripts/link_arize_key.py --account-id <ACCOUNT_ID> --space-id <SPACE_ID>
   ```

3. In the agent settings, select that integration and an AI provider, and set a schedule (for example every Monday).

Inside the sandbox the scripts call `https://graphql.sandboxcontroller/graphql`, and the proxy injects the credential.

## Tests

```bash
python3 -m pytest
```

To try it on synthetic data, preview with `synthetic/assistant_gen.py --dry-run`, then send it to Arize with `--send`. `examples/assistant-synthetic.json` is the matching configuration.
