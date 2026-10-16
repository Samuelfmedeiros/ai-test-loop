# Starting weights per artifact type

Weights are normalised by `loop_runner.py`, so they need not sum to 100 — but keep them readable.
A check with weight 0 is useless (it can never move the score), and a check with weight 80 makes
every other check decoration. Copy the row that matches the artifact and tune after one round.

## Backend / API service

| Check | Command example | Weight |
|---|---|---|
| unit tests | `pytest -q` | 35 |
| integration / e2e | `pytest -q tests/e2e` | 20 |
| type check | `mypy src` | 10 |
| real HTTP call | `curl -sS localhost:8000/health` with `must_match` on a known payload | 15 |
| no secrets | `gitleaks detect --no-banner` with `fail_if_output_matches` | 20 |

## Web app / UI

| Check | Command example | Weight |
|---|---|---|
| build | `npm run build` | 25 |
| unit tests | `npm test -- --run` | 20 |
| e2e smoke | `npx playwright test --grep @smoke` | 25 |
| desktop + mobile screenshot | screenshot script, `must_match` on a non-empty page title | 15 |
| accessibility | `npx axe` or Lighthouse CI budget | 15 |

Visual baselines: a snapshot diff is a check, never a pass by regeneration. If the baseline was
regenerated, say so in the report and attach the before/after diff.

## Data pipeline / migration

| Check | Command example | Weight |
|---|---|---|
| row counts before vs after | script printing `src=… dst=…` with `must_match` on equality | 30 |
| schema matches | `alembic check` / `pg_dump --schema-only` diff | 25 |
| dry run on a copy | pipeline run against a scratch database | 25 |
| no destructive statements | `grep -nE 'DROP|TRUNCATE'` with `fail_if_output_matches` | 20 |

## Document / report

| Check | Command example | Weight |
|---|---|---|
| required sections present | grep for each heading, one check per group | 30 |
| links resolve | `curl -sI` each URL, `expect_exit: 0` | 20 |
| numbers traceable to source | command re-deriving the totals | 30 |
| no placeholders left | `grep -nE 'TODO|TBD|lorem'` with `fail_if_output_matches` | 20 |

## Threshold

- 85-90 for anything user-facing or public.
- 95-100 for money, auth, migrations, or irreversible actions — and the security check must be
  `required: true`.
- Below 85 the loop keeps going; if three rounds move the score by less than 2 points, the checks
  are measuring noise — fix the checks before fixing the code.

## Non-blocking checks

Mark `required: false` for quality-nice-to-haves (coverage delta, bundle-size budget, docs polish).
They still score 0 when they fail and they are printed as `NON_BLOCKING_GAPS`, but they do not gate
delivery. Never mark a security, data-integrity or destructive-operation check as non-blocking.
