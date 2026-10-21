# Cost Model

## IBM Cloud Logs pricing

IBM Cloud Logs bills per GB **ingested** into each pipeline. The three pipelines and their prices (Standard plan):

| Pipeline | Severity levels routed here | Price (USD/GB) |
|---|---|---|
| **Priority Insights** | CRITICAL, ERROR | 1.20 (7-day) / 1.75 (14d) / 2.25 (30d) / 2.75 (60d) / 3.25 (90d) |
| **Analyze & Alert** | INFO, DEBUG | 0.75 |
| **Store & Search** | WARNING, VERBOSE | 0.48 |

The retention window applies only to Priority Insights and is set globally via `FINOPS_PRIORITY_RETENTION_DAYS` (default: 7 days).

> Source: [IBM Cloud Logs — Service plans and pricing](https://cloud.ibm.com/docs/cloud-logs)

---

## Volume estimation

### Static model (no cost report)

When no IBM Cloud Logs usage export is available the model estimates volume from source-code structure:

- **Baseline volume**: number of active log statements × assumed bytes per statement × calls per second.
- **Hotspot multipliers**: statements in loops, retry blocks, or polling functions are multiplied by a configurable factor (default 100×).
- **Level distribution**: the observed fraction of CRITICAL/ERROR/INFO/DEBUG/WARNING/VERBOSE statements is used to apportion volume across pipelines.

The result carries `costBasis: "estimated"` in the API response.

### Calibrated model (with cost report)

IBM Cloud Logs can export daily ingestion summaries as CSV. Place the file at:

```
<FINOPS_COST_REPORTS_DIR>/<repository-name>.csv
```

The expected columns are `date`, `level`, `bytes_ingested`. When a cost report is present:

- Actual GB/day is read directly from the CSV.
- Level distribution is taken from the real data.
- `costBasis` becomes `"calibrated"` in the API response.

---

## Finding types and savings estimation

Each detected finding is assigned `est_savings_pct` — the estimated fraction of the repository's total monthly cost that could be eliminated by acting on the finding:

| Finding type | Optimisation action | Typical savings |
|---|---|---|
| `REQUEST_PAYLOAD` / `RESPONSE_PAYLOAD` | Log only the key fields, not the full object | 10–40% |
| `OBJECT_DUMP` / `JSON_BLOB` | Remove or replace with a concise summary | 5–25% |
| `LOOP_LOGGING` | Move log outside the loop or sample (1-in-N) | 20–60% |
| `RETRY_LOGGING` | Log only on final failure, not each attempt | 5–20% |
| `POLLING_LOGGING` | Log on state transitions only | 10–40% |
| `HIGH_FREQUENCY` | Reduce log volume or upgrade to metrics/traces | 10–30% |
| `MISCLASSIFIED_CRITICAL` | Downgrade to ERROR/WARNING → lower pipeline price | 5–20% |
| `MISCLASSIFIED_ERROR` | Downgrade to WARNING/INFO → lower pipeline price | 2–10% |
| `DUPLICATE_LOGGING` / `REPEATED_METADATA` | Deduplicate or use structured context | 2–15% |
| `LOW_VALUE` | Remove log entirely | 1–5% |

`est_savings_usd_month = est_savings_pct × repository.usd_per_month`

---

## Optimisation rules

The detectors map findings to optimisation rule numbers (referenced in `Finding.rule`):

| Rule | Category |
|---|---|
| 3 | Payload / object dumps |
| 4 | Loop and retry frequency |
| 5 | Polling and consumer frequency |
| 6 | Severity mis-classification |
| 7 | Duplicate and repeated logging |
| 8 | Low-value logging |

---

## PR review thresholds

The PR reviewer returns `BLOCK / WARN / PASS` based on the **net** increase in estimated GB/day from added log statements:

| Decision | Condition |
|---|---|
| BLOCK | Net GB/day added ≥ `FINOPS_PR_HIGH_GB_PER_DAY` (default 5.0) **or** volume growth ≥ `FINOPS_PR_HIGH_GROWTH_PCT` % (default 10%) |
| WARN | Net GB/day added ≥ `FINOPS_PR_MEDIUM_GB_PER_DAY` (default 1.0) **or** growth ≥ `FINOPS_PR_MEDIUM_GROWTH_PCT` % (default 3%) |
| PASS | Below WARN thresholds |

All thresholds are configurable via environment variables (see `backend/app/core/config.py`).
