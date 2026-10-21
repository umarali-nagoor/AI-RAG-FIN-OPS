"""Cross-repository analytics (platform-wide questions)."""

from __future__ import annotations

import re
from typing import Any

from app.models import Impact
from app.services.platform import RepoState


def _fmt_usd(v: float) -> str:
    return f"${v:,.0f}"


def _recent_costly(state: RepoState) -> list[dict[str, Any]]:
    return [
        {"commit": h.sha, "date": h.date, "subject": h.subject, "gbDay": round(h.gb_day, 2), "patterns": h.findings[:3]}
        for h in state.history
        if h.findings and h.gb_day > 0
    ]


def table(states: dict[str, RepoState]) -> list[dict[str, Any]]:
    rows = []
    total_usd = sum(s.analysis.cost.usd_per_month for s in states.values()) or 1.0
    for name, st in states.items():
        a, m = st.analysis, st.analysis.metrics
        rows.append(
            {
                "repository": name,
                "riskLevel": a.risk_level.value,
                "usdPerMonth": a.cost.usd_per_month,
                "gbPerDay": a.cost.gb_per_day,
                "costBasis": a.cost.basis,
                "platformSharePct": a.cost.platform_share_pct
                if a.cost.platform_share_pct is not None
                else round(a.cost.usd_per_month / total_usd * 100, 1),
                "optimizationPotentialPct": a.potential_pct,
                "optimizationPotentialUsdMonth": m["potential_usd_month"],
                "payloadStatements": m["payload_statements"],
                "payloadGbDay": m["payload_est_gb_day"],
                "largestPayloadBytes": m["largest_payload_bytes"],
                "duplicateStatements": m["duplicate_statements"],
                "errorStatements": m["error_statements"],
                "errorRatioPct": m["error_ratio_pct"],
                "misclassifiedErrors": m["misclassified_error_statements"],
                "recentCostlyCommits": len(_recent_costly(st)),
                "statements": m["statements"],
            }
        )
    return rows


METRICS: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "recent",
        re.compile(r"recent|introduc|lately|last (\d+ )?(commit|week|month)|new(ly)? added", re.I),
        "recentCostlyCommits",
        "recently introduced costly logging patterns",
    ),
    ("duplicates", re.compile(r"duplicat", re.I), "duplicateStatements", "duplicate logging statements"),
    ("errors", re.compile(r"\berror|critical|severity", re.I), "errorRatioPct", "ERROR/CRITICAL logging share"),
    (
        "payload",
        re.compile(r"payload|largest|object|request|response|body|size", re.I),
        "payloadGbDay",
        "estimated payload-logging volume",
    ),
    (
        "potential",
        re.compile(r"optimi[sz]ation|opportunit|potential|saving|reduce", re.I),
        "optimizationPotentialUsdMonth",
        "optimisation opportunity",
    ),
    ("cost", re.compile(r"cost|spend|expensive|bill|usd|\$|gb", re.I), "usdPerMonth", "estimated logging cost"),
]


def pick_metric(question: str) -> tuple[str, str, str]:
    for key, rx, col, label in METRICS:
        if rx.search(question):
            return key, col, label
    return "cost", "usdPerMonth", "estimated logging cost"


def answer(question: str, states: dict[str, RepoState], top: int = 5) -> tuple[str, dict[str, Any]]:
    rows = table(states)
    if not rows:
        return "No repositories are available for analysis.", {"rows": []}
    key, col, label = pick_metric(question)
    ranked = sorted(rows, key=lambda r: -float(r[col]))
    lines = [f"Repositories ranked by {label}:"]
    for i, r in enumerate(ranked[:top], 1):
        lines.append(f"{i}. {r['repository']} — {_detail(key, r)}")
    if key == "recent":
        for r in ranked[:top]:
            commits = _recent_costly(states[r["repository"]])
            for c in commits[:2]:
                lines.append(
                    f"   - {r['repository']} {c['commit']} ({c['date']}): {c['subject']} → {c['gbDay']:+.2f} GB/day; "
                    f"{'; '.join(c['patterns'])}"
                )
    total = sum(r["usdPerMonth"] for r in rows)
    lines.append(
        f"Platform total: {_fmt_usd(total)}/month across {len(rows)} repositories "
        f"({sum(1 for r in rows if r['riskLevel'] == Impact.HIGH.value)} HIGH risk)."
    )
    return "\n".join(lines), {"metric": col, "label": label, "ranking": ranked, "rows": rows}


def _detail(key: str, r: dict[str, Any]) -> str:
    base = f"{_fmt_usd(r['usdPerMonth'])}/month, {r['gbPerDay']:.1f} GB/day ({r['platformSharePct']}% of platform)"
    return {
        "cost": base,
        "potential": f"{r['optimizationPotentialPct']}% potential ≈ {_fmt_usd(r['optimizationPotentialUsdMonth'])}/month "
        f"(of {_fmt_usd(r['usdPerMonth'])})",
        "payload": f"{r['payloadStatements']} payload-logging sites ≈ {r['payloadGbDay']:.1f} GB/day, "
        f"largest ≈ {r['largestPayloadBytes']:,} bytes/record",
        "duplicates": f"{r['duplicateStatements']} duplicate logging sites",
        "errors": f"{r['errorStatements']} ERROR/CRITICAL statements ({r['errorRatioPct']}% of {r['statements']}); "
        f"{r['misclassifiedErrors']} look misclassified",
        "recent": f"{r['recentCostlyCommits']} recent commits added costly logging patterns",
    }[key]
