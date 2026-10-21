#!/usr/bin/env python3
"""Sync IBM Cloud Logs data-usage reports into the FinOps Copilot cost-reports directory.

Usage
-----
    python scripts/sync_cost_reports.py \
        --instance-id  <ICL_INSTANCE_GUID> \
        --region       us-south \
        --range        last_30_days \
        --out-dir      sample-data/cost-reports \
        --mapping      config/icl_applications.yaml \
        --reload-url   http://127.0.0.1:8080/api/admin/reload-cost-reports

Environment variables (alternative to flags — never put secrets in CLI args):
    ICL_INSTANCE_ID   IBM Cloud Logs instance GUID
    ICL_REGION        e.g. us-south, eu-de, eu-gb
    IBM_CLOUD_API_KEY IBM Cloud API key (used to obtain an IAM token)

The script writes one JSON file per calendar month found in the report, e.g.
    cost-reports/2025-07.json

It then calls POST /api/admin/reload-cost-reports so the running server picks up
the new data without a restart.

Requirements: requests, PyYAML  (pip install requests pyyaml)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

logging.basicConfig(format="%(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("sync_cost_reports")

# IBM Cloud Logs severity → FinOps Copilot Level
SEVERITY_MAP = {
    "critical": "CRITICAL",
    "error":    "ERROR",
    "warning":  "WARNING",
    "warn":     "WARNING",
    "info":     "INFO",
    "debug":    "DEBUG",
    "trace":    "VERBOSE",
    "verbose":  "VERBOSE",
}

# IBM Cloud Logs pricing (USD per GB ingested, Standard plan)
PRIORITY_PRICE = {"high": 1.20, "medium": 0.75, "low": 0.48, "blocked": 0.0}

# Severity → TCO pipeline (matches app/core/pricing.py)
SEVERITY_PRIORITY = {
    "CRITICAL": "high", "ERROR": "high",
    "WARNING": "low",
    "INFO": "medium", "DEBUG": "medium",
    "VERBOSE": "low",
}


# ── IAM authentication ────────────────────────────────────────────────────────

def get_iam_token(api_key: str) -> str:
    """Exchange an IBM Cloud API key for a short-lived IAM bearer token."""
    resp = requests.post(
        "https://iam.cloud.ibm.com/identity/token",
        data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": api_key},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── IBM Cloud Logs data-usage API ─────────────────────────────────────────────

def fetch_detailed_usage(instance_id: str, region: str, iam_token: str, range_: str) -> list[dict]:
    """Call GET /v1/data_usage?query=detailed and return the rows list."""
    url = f"https://{instance_id}.api.{region}.logs.cloud.ibm.com/v1/data_usage"
    resp = requests.get(
        url,
        params={"query": "detailed", "range": range_},
        headers={
            "Authorization": f"Bearer {iam_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=60,
    )
    if resp.status_code == 403:
        raise SystemExit(
            f"ERROR 403 Forbidden — the API key does not have the required IAM role on instance {instance_id}.\n"
            "Grant the 'Reader' (or 'Manager') role:\n"
            "  IBM Cloud console → Manage → Access (IAM) → Service IDs → <your service ID>\n"
            "  → Access → Assign access → IBM Cloud Logs → <instance> → Reader\n"
            "Then wait ~1 minute and retry."
        )
    resp.raise_for_status()
    data = resp.json()
    # The API returns {"data_usages": [...]} (confirmed against live API)
    # Fall back to other shapes for forward-compatibility
    if isinstance(data, list):
        return data
    return (
        data.get("data_usages")
        or data.get("data")
        or data.get("rows")
        or []
    )


# ── Mapping: ICL application/subsystem → repository name ─────────────────────

def load_mapping(path: Path) -> dict[tuple[str, str], str]:
    """Load config/icl_applications.yaml → {(application, subsystem): repository}."""
    if not path.exists():
        log.warning("Mapping file not found: %s — using subsystem_name as repository name", path)
        return {}
    raw = yaml.safe_load(path.read_text())
    result: dict[tuple[str, str], str] = {}
    for entry in raw.get("applications", []):
        app = (entry.get("icl_application", "").lower(), entry.get("icl_subsystem", "").lower())
        result[app] = entry["repository"]
    return result


def resolve_repo(row: dict, mapping: dict[tuple[str, str], str]) -> str:
    """Map an ICL row to a repository name.

    The live IBM Cloud Logs API uses:
      'application'  (not 'application_name')
      'subsytem'     (single-s typo in the API — not 'subsystem_name')
    We accept all variants for forward-compatibility.
    """
    app = (row.get("application") or row.get("application_name") or "").lower()
    sub = (row.get("subsytem") or row.get("subsystem_name") or row.get("subsystem") or "").lower()
    return mapping.get((app, sub)) or mapping.get(("", sub)) or sub or app or "unknown"


# ── Aggregate rows into per-month cost-report JSON ────────────────────────────

def aggregate(rows: list[dict], mapping: dict[tuple[str, str], str]) -> dict[str, dict]:
    """
    Group detailed-usage rows by (month, repository, severity).

    Returns: { "YYYY-MM": { "repo": { "gb_per_day": float, "by_severity_gb_day": {...} } } }
    """
    # { month: { repo: { severity: total_gb } } }
    buckets: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    day_counts: dict[str, set[str]] = defaultdict(set)  # month → set of dates (to compute avg GB/day)

    for row in rows:
        # Live API uses 'timestamp' (ISO-8601); also accept 'date' for compatibility
        raw_ts = row.get("timestamp") or row.get("date") or ""
        date_str = raw_ts[:10]  # "YYYY-MM-DD"
        if not date_str:
            continue
        month = date_str[:7]  # "YYYY-MM"
        day_counts[month].add(date_str)

        # Live API field is 'size' (GB); accept 'total_data_sent_gb' and 'gb' for compatibility
        gb = float(row.get("size") or row.get("total_data_sent_gb") or row.get("gb") or 0)
        severity_raw = row.get("severity", "info").lower()
        severity = SEVERITY_MAP.get(severity_raw, "INFO")
        repo = resolve_repo(row, mapping)

        buckets[month][repo][severity] += gb

    result: dict[str, dict] = {}
    for month, repos in buckets.items():
        days = max(len(day_counts[month]), 1)
        services: dict[str, dict] = {}
        total_usd = 0.0
        for repo, by_sev in repos.items():
            total_gb = sum(by_sev.values())
            gb_per_day = total_gb / days
            # Estimate USD: each severity → pipeline → price
            usd = sum(
                gb_val / days * 30 * PRIORITY_PRICE[SEVERITY_PRIORITY.get(sev, "medium")]
                for sev, gb_val in by_sev.items()
            )
            total_usd += usd
            services[repo] = {
                "gb_per_day": round(gb_per_day, 2),
                "usd_month": round(usd, 2),
                "by_severity_gb_day": {
                    sev: round(gb_val / days, 2) for sev, gb_val in sorted(by_sev.items())
                },
            }
        result[month] = {
            "period": month,
            "total_usd_month": round(total_usd, 2),
            "notes": f"Generated by sync_cost_reports.py on {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "services": services,
        }
    return result


# ── Write files + reload ──────────────────────────────────────────────────────

def write_reports(reports: dict[str, dict], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for month, report in reports.items():
        path = out_dir / f"{month}.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info("wrote %s (%d services)", path, len(report["services"]))
        written.append(path)
    return written


def reload_server(reload_url: str) -> None:
    try:
        resp = requests.post(reload_url, timeout=10)
        resp.raise_for_status()
        log.info("server reloaded: %s", resp.json())
    except Exception as exc:
        log.warning("could not reload server (is it running?): %s", exc)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--instance-id",  default=os.environ.get("ICL_INSTANCE_ID"),   help="IBM Cloud Logs instance GUID")
    p.add_argument("--region",       default=os.environ.get("ICL_REGION", "us-south"), help="IBM Cloud region, e.g. us-south")
    p.add_argument("--range",        default="last_30_days",
                   choices=["current_month", "last_30_days", "last_90_days", "last_week"])
    p.add_argument("--api-key",      default=os.environ.get("IBM_CLOUD_API_KEY"),  help="IBM Cloud API key")
    p.add_argument("--out-dir",      default="cost-reports",                        type=Path,
                   help="Output directory for generated cost-report JSON files. "
                        "Use a separate directory from sample-data/cost-reports to avoid overwriting sample data.")
    p.add_argument("--mapping",      default="config/icl_applications.yaml",       type=Path)
    p.add_argument("--reload-url",   default="http://127.0.0.1:8080/api/admin/reload-cost-reports")
    p.add_argument("--dry-run",      action="store_true", help="Print JSON, do not write files")
    args = p.parse_args()

    if not args.instance_id:
        p.error("--instance-id / ICL_INSTANCE_ID is required")
    if not args.api_key:
        p.error("--api-key / IBM_CLOUD_API_KEY is required")

    log.info("authenticating with IBM Cloud IAM…")
    token = get_iam_token(args.api_key)

    log.info("fetching detailed usage from %s / %s (range=%s)…", args.instance_id, args.region, args.range)
    rows = fetch_detailed_usage(args.instance_id, args.region, token, args.range)
    log.info("received %d rows", len(rows))

    if not rows:
        log.error("no data returned — check instance ID, region, and IAM permissions (needs Reader role)")
        sys.exit(1)

    mapping = load_mapping(args.mapping)
    reports = aggregate(rows, mapping)

    if args.dry_run:
        print(json.dumps(reports, indent=2))
        return

    write_reports(reports, args.out_dir)
    reload_server(args.reload_url)
    log.info("done — %d month(s) written", len(reports))


if __name__ == "__main__":
    main()
