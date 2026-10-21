"""Repository analyzer: extraction -> cost model -> detectors -> risk & savings."""

from __future__ import annotations

from collections import defaultdict

from app.analysis.cost import Calibration, CostModel
from app.analysis.detectors import Detector
from app.core.config import Settings
from app.core.pricing import Level
from app.models import (
    CONFIDENCE_WEIGHT,
    PAYLOAD_FINDING_TYPES,
    Finding,
    FindingType,
    Impact,
    LogStatement,
    RepoAnalysis,
    TopReason,
)

HIGH_IMPACT_SHARE = 8.0
MEDIUM_IMPACT_SHARE = 2.0


def impact_for_share(share_pct: float) -> Impact:
    if share_pct >= HIGH_IMPACT_SHARE:
        return Impact.HIGH
    if share_pct >= MEDIUM_IMPACT_SHARE:
        return Impact.MEDIUM
    return Impact.LOW


MAX_POTENTIAL_PCT = 60.0  # entry/exit, outcome and error logs always remain; static analysis never claims more
REALISATION_FACTOR = 0.5  # share of confidence-weighted savings assumed achievable (low end of the range)


def _combined_savings(findings: list[Finding], model: CostModel, by_id: dict[str, LogStatement]) -> tuple[float, float]:
    """Return (raw, confidence-weighted) USD/month savings.

    Several findings can hit the same statement (e.g. polling + object dump). Their reductions compound
    multiplicatively on that statement's own cost, so a statement can never save more than it costs.
    """
    cost = {f"{s.file}:{s.line}": model.usd_month(s) for s in by_id.values()}
    remaining_raw: dict[str, float] = {}
    remaining_weighted: dict[str, float] = {}
    for f in findings:
        key = f"{f.file}:{f.line}"
        c = cost.get(key, 0.0)
        if c <= 0:
            continue
        frac = min(f.est_savings_usd_month / c, 1.0)
        remaining_raw[key] = remaining_raw.get(key, 1.0) * (1 - frac)
        remaining_weighted[key] = remaining_weighted.get(key, 1.0) * (1 - frac * CONFIDENCE_WEIGHT[f.confidence])
    raw = sum(cost[k] * (1 - r) for k, r in remaining_raw.items())
    weighted = sum(cost[k] * (1 - r) for k, r in remaining_weighted.items())
    return raw, weighted


def analyze_statements(
    settings: Settings,
    repository: str,
    statements: list[LogStatement],
    *,
    calibration: Calibration | None = None,
    files_scanned: int = 0,
    languages: list[str] | None = None,
    commit: str | None = None,
) -> RepoAnalysis:
    model = CostModel(settings, statements, calibration)
    cost = model.summarize(statements)
    findings = Detector(settings, model, statements).run()
    by_id = {s.id: s for s in statements}
    total_usd = sum(model.usd_month(s) for s in statements)

    raw, weighted = _combined_savings(findings, model, by_id)
    pct_high = min(raw / total_usd * 100, MAX_POTENTIAL_PCT) if total_usd else 0.0
    pct_low = min(weighted / total_usd * 100 * REALISATION_FACTOR, pct_high) if total_usd else 0.0
    pct_mid = (pct_high + pct_low) / 2

    # --- group findings into top reasons -----------------------------------------------------
    groups: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        groups[f.title].append(f)
    reasons: list[TopReason] = []
    for title, items in groups.items():
        share = sum(i.est_savings_usd_month for i in items) / total_usd * 100 if total_usd else 0.0
        reasons.append(
            TopReason(
                reason=title,
                impact=impact_for_share(share),
                count=len(items),
                share_pct=round(share, 1),
                files=sorted(
                    {i.file for i in items}, key=lambda f: -sum(x.est_savings_usd_month for x in items if x.file == f)
                )[:5],
            )
        )
    reasons.sort(key=lambda r: -r.share_pct)

    high_reasons = sum(1 for r in reasons if r.impact is Impact.HIGH)
    if pct_mid >= 20 or high_reasons >= 2:
        risk = Impact.HIGH
    elif pct_mid >= 8 or high_reasons == 1 or any(r.impact is Impact.MEDIUM for r in reasons[:3]) and pct_mid >= 5:
        risk = Impact.MEDIUM
    else:
        risk = Impact.LOW

    level_counts = {lv.value: sum(1 for s in statements if s.level is lv) for lv in Level}
    err_like = level_counts["ERROR"] + level_counts["CRITICAL"]
    payload_findings = [f for f in findings if f.type in PAYLOAD_FINDING_TYPES]
    metrics = {
        "statements": len(statements),
        "by_level": level_counts,
        "error_statements": err_like,
        "error_ratio_pct": round(err_like / len(statements) * 100, 1) if statements else 0.0,
        "payload_statements": len(payload_findings),
        "payload_est_gb_day": round(sum(model.gb_day(s) * model.payload_fraction(s) for s in statements), 3),
        "largest_payload_bytes": max((_payload_bytes(s) for s in statements), default=0),
        "duplicate_statements": sum(1 for f in findings if f.type is FindingType.DUPLICATE_LOGGING),
        "misclassified_error_statements": sum(1 for f in findings if f.type is FindingType.MISCLASSIFIED_ERROR),
        "high_frequency_statements": sum(
            1
            for f in findings
            if f.type
            in {
                FindingType.LOOP_LOGGING,
                FindingType.RETRY_LOGGING,
                FindingType.POLLING_LOGGING,
                FindingType.HIGH_FREQUENCY,
            }
        ),
        "potential_usd_month": round(raw, 2),
    }
    return RepoAnalysis(
        repository=repository,
        commit=commit,
        languages=languages or sorted({s.language for s in statements}),
        files_scanned=files_scanned,
        statements=statements,
        findings=findings,
        cost=cost,
        risk_level=risk,
        potential_pct=round(pct_mid, 1),
        potential_low_pct=round(pct_low, 1),
        potential_high_pct=round(pct_high, 1),
        top_reasons=reasons,
        metrics=metrics,
    )


def _payload_bytes(s: LogStatement) -> int:
    from app.analysis.cost import payload_bytes

    return int(payload_bytes(s))
