"""Proactive PR review: Code -> Logs -> Cost.

For every changed Go/Java/Python file we build the *post-PR* file (base checkout + hunks, falling back to the
hunks alone), extract its logging statements, and run the same detectors as the repository analysis on the
post-PR state of the repository. Only findings that land on added lines are reported, and the net GB/day
change is computed as (added statements) - (removed statements) using the repository's calibrated model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.analysis.cost import Calibration, CostModel
from app.analysis.detectors import Detector
from app.core.config import Settings
from app.core.pricing import Level
from app.ingest.diff import FileDiff, apply_hunks, parse_diff, reconstruct_from_hunks, reconstruct_old_from_hunks
from app.ingest.extractor import LANGUAGES, detect_language, extract_file
from app.ingest.repos import read_base_file
from app.models import Confidence, Finding, FindingType, Impact, LogStatement

TYPE_LABEL = {
    FindingType.REQUEST_PAYLOAD: "Payload Logging",
    FindingType.RESPONSE_PAYLOAD: "Payload Logging",
    FindingType.OBJECT_DUMP: "Object Logging",
    FindingType.JSON_BLOB: "Oversized Payload Logging",
    FindingType.LOOP_LOGGING: "Loop Logging",
    FindingType.RETRY_LOGGING: "Retry Logging",
    FindingType.POLLING_LOGGING: "Polling Logging",
    FindingType.HIGH_FREQUENCY: "High-Frequency Logging",
    FindingType.LOW_VALUE: "Low-Value Logging",
    FindingType.MISCLASSIFIED_ERROR: "Incorrect ERROR Severity",
    FindingType.MISCLASSIFIED_CRITICAL: "Incorrect CRITICAL Severity",
    FindingType.DUPLICATE_LOGGING: "Duplicate Logging",
    FindingType.REPEATED_METADATA: "Repeated Metadata",
}


@dataclass
class PRFinding:
    type: str
    reason: str
    location: str
    level: Level
    impact: Impact
    gb_day: float
    recommendation: str
    suggested_code: str | None = None
    rule: int = 0


@dataclass
class PRResult:
    risk: Impact
    decision: str
    net_gb_day: float
    added_gb_day: float
    removed_gb_day: float
    net_usd_month: float
    growth_pct: float
    findings: list[PRFinding] = field(default_factory=list)
    new_statements: list[LogStatement] = field(default_factory=list)
    removed_statements: int = 0
    files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)


def _decode(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", "replace")


def _build_sides(fd: FileDiff, root: Path | None, max_bytes: int) -> tuple[str, str]:
    """Return (old_text, new_text) for a changed file."""
    base = _decode(read_base_file(root, fd.old_path or fd.path, max_bytes)) if root and not fd.is_new else None
    if base is not None:
        new = apply_hunks(base, fd.hunks)
        if new is not None:
            return base, new
    return reconstruct_old_from_hunks(fd.hunks), reconstruct_from_hunks(fd.hunks)


def _key(s: LogStatement) -> tuple:
    return (s.level, s.message, tuple(s.args), s.file)


def _touches(s: LogStatement, lines: set[int]) -> bool:
    return any(n in lines for n in range(s.line, s.end_line + 1))


def review_diff(
    settings: Settings,
    repository: str,
    diff_text: str,
    baseline: list[LogStatement],
    calibration: Calibration | None,
    root: Path | None,
) -> PRResult:
    files = parse_diff(diff_text)
    changed_paths = {f.path for f in files} | {f.old_path for f in files if f.old_path}
    model = CostModel(settings, baseline, calibration)
    baseline_total = sum(model.gb_day(s) for s in baseline)
    baseline_usd = sum(model.usd_month(s) for s in baseline)

    new_all: list[LogStatement] = []
    added: list[LogStatement] = []
    removed: list[LogStatement] = []
    reviewed: list[str] = []
    skipped: list[str] = []

    for fd in files:
        lang = detect_language(fd.path) or (detect_language(fd.old_path) if fd.old_path else None)
        if lang not in LANGUAGES:
            skipped.append(fd.path)
            continue
        reviewed.append(fd.path)
        old_text, new_text = _build_sides(fd, root, settings.max_file_bytes)
        if not fd.is_deleted:
            stmts = extract_file(repository, fd.path, new_text.encode(), lang)
            add_lines = fd.added_lines
            for s in stmts:
                if _touches(s, add_lines):
                    s.is_new = True
                    added.append(s)
            new_all.extend(stmts)
        if not fd.is_new:
            rem_lines = fd.removed_lines
            old_stmts = extract_file(repository, fd.old_path or fd.path, old_text.encode(), lang)
            removed.extend(s for s in old_stmts if _touches(s, rem_lines))

    # An unchanged-text statement that merely shifts position is not "added"; match by (message, args, level).
    removed_keys = {_key(s) for s in removed}
    added_keys = {_key(s) for s in added}
    truly_added = [s for s in added if _key(s) not in removed_keys]
    truly_removed = [s for s in removed if _key(s) not in added_keys]
    added_gb = sum(model.gb_day(s) for s in truly_added)
    removed_gb = sum(model.gb_day(s) for s in truly_removed)
    # statements present on both sides (edited in place) contribute only their delta
    changed_pairs = [s for s in added if s not in truly_added]
    for a in changed_pairs:
        match = next((r for r in removed if _key(r) == _key(a)), None)
        if match is None:
            continue
        delta = model.gb_day(a) - model.gb_day(match)
        added_gb += max(delta, 0.0)
        removed_gb += max(-delta, 0.0)
    net_gb = added_gb - removed_gb

    # --- detectors on the post-PR repository state ---------------------------------------------------
    post = [s for s in baseline if s.file not in changed_paths] + new_all
    detector = Detector(settings, model, post)
    new_keys = {(s.file, s.line) for s in truly_added + changed_pairs}
    stmt_by_key = {(s.file, s.line): s for s in post}
    findings: list[PRFinding] = []
    flagged: set[tuple[str, int]] = set()
    for f in detector.run():
        key = (f.file, f.line)
        if key not in new_keys:
            continue
        flagged.add(key)
        st = stmt_by_key[key]
        findings.append(_to_pr_finding(f, model.gb_day(st)))

    for key in sorted(new_keys - flagged):
        st = stmt_by_key[key]
        if st.level in (Level.ERROR, Level.CRITICAL) and (
            st.in_loop or st.in_polling or st.in_consumer or st.in_retry or st.level is Level.CRITICAL
        ):
            gb = model.gb_day(st)
            findings.append(
                PRFinding(
                    type=f"New {st.level.value} Log",
                    location=f"{st.file}:{st.line}",
                    level=st.level,
                    impact=Impact.MEDIUM if st.level is Level.ERROR else Impact.HIGH,
                    gb_day=gb,
                    rule=5,
                    reason=f"New {st.level.value} log routes to Priority Insights "
                    f"(${model.price(st.level):.2f}/GB) and can trigger alerts/pages"
                    f"{' from a repeatedly-invoked path' if st.in_loop or st.in_consumer or st.in_polling else ''}.",
                    recommendation="Confirm this is an unexpected, actionable failure. Use WARNING (or INFO) for "
                    "expected/recoverable conditions; reserve CRITICAL for data-loss or fatal conditions.",
                )
            )

    # --- risk ----------------------------------------------------------------------------------------
    growth = (net_gb / baseline_total * 100) if baseline_total > 0 else 0.0
    if net_gb >= settings.pr_high_gb_per_day or growth >= settings.pr_high_growth_pct:
        risk = Impact.HIGH
    elif net_gb >= settings.pr_medium_gb_per_day or growth >= settings.pr_medium_growth_pct:
        risk = Impact.MEDIUM
    else:
        risk = Impact.LOW
    high_conf_payload = any(f.rule == 3 and f.impact is not Impact.LOW for f in findings)
    new_critical = any(f.type == "New CRITICAL Log" for f in findings)
    if risk is Impact.LOW and (high_conf_payload or new_critical or any(f.impact is Impact.HIGH for f in findings)):
        risk = Impact.MEDIUM
    if net_gb <= 0 and not findings:
        risk = Impact.LOW

    findings.sort(key=lambda f: (-{"HIGH": 2, "MEDIUM": 1, "LOW": 0}[f.impact.value], -f.gb_day))
    decision = "BLOCK" if risk is Impact.HIGH else "WARN" if risk is Impact.MEDIUM else "PASS"
    usd = (
        net_gb
        * 30
        * (baseline_usd / (baseline_total * 30) if baseline_total > 0 else _avg_price(settings, truly_added))
    )
    return PRResult(
        risk=risk,
        decision=decision,
        net_gb_day=round(net_gb, 3),
        added_gb_day=round(added_gb, 3),
        removed_gb_day=round(removed_gb, 3),
        net_usd_month=round(usd, 2),
        growth_pct=round(growth, 1),
        findings=findings,
        new_statements=truly_added,
        removed_statements=len(truly_removed),
        files=reviewed,
        skipped_files=skipped,
    )


def _avg_price(settings: Settings, stmts: list[LogStatement]) -> float:
    from app.core.pricing import price_per_gb

    if not stmts:
        return price_per_gb(Level.INFO, settings.priority_retention_days)
    return sum(price_per_gb(s.level, settings.priority_retention_days) for s in stmts) / len(stmts)


def _to_pr_finding(f: Finding, gb_day: float) -> PRFinding:
    impact = f.impact
    if f.confidence is Confidence.LOW and impact is Impact.HIGH:
        impact = Impact.MEDIUM
    return PRFinding(
        type=TYPE_LABEL.get(f.type, f.title),
        reason=f.reason,
        location=f"{f.file}:{f.line}",
        level=f.level,
        impact=impact,
        gb_day=gb_day,
        recommendation=f.recommendation,
        suggested_code=f.suggested_code,
        rule=f.rule,
    )
