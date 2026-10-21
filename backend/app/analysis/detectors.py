"""Rule detectors (optimisation rules 1-8, see knowledge/optimization-standards.md).

Guardrails baked in:
  Rule 1  function entry/exit logs are never flagged for removal.
  Rule 2  INFO is never auto-downgraded to DEBUG; DEBUG/VERBOSE are assumed disabled in production (zero cost).
  Rule 3  whole-object / request / response / payload logging is the highest-priority finding.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from app.analysis.cost import CostModel
from app.core.config import Settings
from app.core.pricing import Level, Pipeline, pipeline_for, price_per_gb
from app.models import (
    Confidence,
    Finding,
    FindingType,
    Impact,
    LogStatement,
    PayloadKind,
)

# Savings fractions of a statement's own volume when the recommended pattern is applied
POLLING_SAVINGS = 0.80  # log on state change / every N polls
LOOP_SAVINGS = 0.60  # one summary after the loop instead of one record per iteration
RETRY_SAVINGS = 0.50  # log first/last attempt only
CONSUMER_SAVINGS = 0.30
LOW_VALUE_SAVINGS = 0.70
METADATA_SAVINGS = 0.25

_LOW_VALUE = re.compile(
    r"^\W*(starting|started|start|begin|beginning|processing|process|handling|handled|received|finished|"
    r"finish|done|completed|complete|ending|end|running|executing|executed|working on)\b"
    r"[\w\s:.-]{0,40}$",
    re.I,
)
_METADATA_KEYS = re.compile(
    r"\b(region|cluster|env|environment|version|hostname|host|service|servicename|app|appname|namespace|"
    r"datacenter|zone|pod|node|build|commit)\b",
    re.I,
)
_VERB = re.compile(r"%[+#]?[vsdqrx]|\{\}|\{[\w.]*\}|:\s*$")

_TYPE_RULE = {
    FindingType.REQUEST_PAYLOAD: 3,
    FindingType.RESPONSE_PAYLOAD: 3,
    FindingType.OBJECT_DUMP: 3,
    FindingType.JSON_BLOB: 8,
    FindingType.LOW_VALUE: 4,
    FindingType.MISCLASSIFIED_ERROR: 5,
    FindingType.MISCLASSIFIED_CRITICAL: 5,
    FindingType.DUPLICATE_LOGGING: 6,
    FindingType.LOOP_LOGGING: 7,
    FindingType.RETRY_LOGGING: 7,
    FindingType.POLLING_LOGGING: 7,
    FindingType.HIGH_FREQUENCY: 7,
    FindingType.REPEATED_METADATA: 7,
}


def _fid(kind: FindingType, s: LogStatement) -> str:
    return hashlib.sha1(f"{kind}:{s.id}".encode()).hexdigest()[:10]  # noqa: S324


def _id_name(expr: str, kind: PayloadKind, lang: str) -> str:
    snake = lang == "python"
    if kind in (PayloadKind.REQUEST, PayloadKind.RESPONSE, PayloadKind.PAYLOAD, PayloadKind.JSON_BLOB):
        return "request_id" if snake else "requestId"
    base = re.sub(r"\[.*?\]|\(.*?\)", "", expr).split(".")[-1].strip("&*")
    base = re.sub(r"(Object|Obj|Resource|Dto|DTO|Spec|Map|List)$", "", base) or "resource"
    if snake:
        base = re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
        return f"{base}_id"
    return f"{base[0].lower()}{base[1:]}Id"


def suggest_payload_fix(s: LogStatement) -> str:
    """Return a one-line replacement that logs identifiers instead of the whole object."""
    pa = s.payload_args[0]
    ident = _id_name(pa.expr, pa.kind, s.language)
    msg = _VERB.sub("", s.message).strip(" :-") or "operation"
    call = s.logger_call
    if s.language == "go":
        if call.rsplit(".", 1)[-1].lower().endswith("f"):
            return f'{call}("{msg} {ident}=%s", {ident})'
        return f'{call}("{msg}", "{ident}", {ident})'
    if s.language == "java":
        return f'{call}("{msg} {ident}={{}}", {ident});'
    return f'{call}("{msg} {ident}=%s", {ident})'


class Detector:
    def __init__(self, settings: Settings, cost: CostModel, statements: list[LogStatement]):
        self.settings = settings
        self.cost = cost
        self.statements = statements
        self.total_usd = sum(cost.usd_month(s) for s in statements) or 0.0
        self._funcs: dict[str, list[LogStatement]] = defaultdict(list)
        for s in statements:
            if s.function:
                self._funcs[s.function].append(s)
        self._msg_counts: dict[str, int] = defaultdict(int)
        for s in statements:
            self._msg_counts[self._norm(s.message)] += 1

    @staticmethod
    def _norm(msg: str) -> str:
        return re.sub(r"\W+", " ", msg.lower()).strip()

    # -- helpers ----------------------------------------------------------------------------
    def _billable(self, s: LogStatement) -> bool:
        """Rule 2: DEBUG/VERBOSE are disabled in production and therefore cost nothing."""
        return s.level not in (Level.DEBUG, Level.VERBOSE) or self.settings.debug_enabled_in_prod

    def _make(
        self,
        kind: FindingType,
        s: LogStatement,
        *,
        title: str,
        reason: str,
        recommendation: str,
        fraction: float,
        confidence: Confidence,
        suggested: str | None = None,
        related: list[str] | None = None,
        price_ratio: float = 1.0,
    ) -> Finding:
        """`fraction` = share of the statement's bytes removed; `price_ratio` < 1 models moving pipeline."""
        gb = self.cost.gb_day(s)
        usd = self.cost.usd_month(s)
        saved_usd = usd * (1 - (1 - fraction) * price_ratio)
        saved_gb = gb * fraction
        share = (saved_usd / self.total_usd * 100) if self.total_usd else 0.0
        impact = Impact.HIGH if share >= 8 else Impact.MEDIUM if share >= 2 else Impact.LOW
        if (
            kind in (FindingType.REQUEST_PAYLOAD, FindingType.RESPONSE_PAYLOAD, FindingType.OBJECT_DUMP)
            and confidence is Confidence.HIGH
            and impact is Impact.LOW
        ):
            impact = Impact.MEDIUM
        return Finding(
            id=_fid(kind, s),
            type=kind,
            rule=_TYPE_RULE[kind],
            title=title,
            impact=impact,
            confidence=confidence,
            repository=s.repository,
            file=s.file,
            line=s.line,
            function=s.function,
            level=s.level,
            snippet=s.snippet,
            reason=reason,
            recommendation=recommendation,
            suggested_code=suggested,
            related=related or [],
            est_savings_pct=round(share, 2),
            est_savings_usd_month=round(saved_usd, 2),
            est_gb_day_saved=round(saved_gb, 4),
        )

    # -- Rule 3 / 8: payload logging ----------------------------------------------------------
    def payloads(self) -> list[Finding]:
        out = []
        for s in self.statements:
            if not s.payload_args or not self._billable(s):
                continue
            pa = max(s.payload_args, key=lambda p: {"HIGH": 2, "MEDIUM": 1, "LOW": 0}[p.confidence.value])
            kind = {
                PayloadKind.REQUEST: FindingType.REQUEST_PAYLOAD,
                PayloadKind.RESPONSE: FindingType.RESPONSE_PAYLOAD,
                PayloadKind.PAYLOAD: FindingType.OBJECT_DUMP,
                PayloadKind.OBJECT: FindingType.OBJECT_DUMP,
                PayloadKind.COLLECTION: FindingType.JSON_BLOB,
                PayloadKind.JSON_BLOB: FindingType.JSON_BLOB,
            }[pa.kind]
            titles = {
                FindingType.REQUEST_PAYLOAD: "Full request payload logging",
                FindingType.RESPONSE_PAYLOAD: "Response object logging",
                FindingType.OBJECT_DUMP: "Complete object logging",
                FindingType.JSON_BLOB: "Oversized JSON / collection logging",
            }
            ident = _id_name(pa.expr, pa.kind, s.language)
            where = "inside a loop, " if s.in_loop else "in a high-frequency consumer, " if s.in_consumer else ""
            reason = (
                f"`{pa.expr}` is serialised in full into a {s.level.value} record {where}"
                f"adding roughly {int(self.cost.payload_fraction(s) * 100)}% of the record's bytes "
                f"({pipeline_for(s.level).value}, ${price_per_gb(s.level, self.settings.priority_retention_days):.2f}/GB)."
            )
            out.append(
                self._make(
                    kind,
                    s,
                    title=titles[kind],
                    reason=reason,
                    recommendation=f"Log the identifier (`{ident}`) instead of the whole `{pa.expr}`; "
                    "keep the full object out of ingestion (reproduce via the ID in your datastore/APIs).",
                    fraction=self.cost.payload_fraction(s),
                    confidence=pa.confidence,
                    suggested=suggest_payload_fix(s),
                )
            )
        return out

    # -- Rule 7: high-frequency logging --------------------------------------------------------
    def frequency(self) -> list[Finding]:
        out = []
        for s in self.statements:
            if not self._billable(s) or s.entry_exit or s.in_init:  # Rule 1: never touch entry/exit logs
                continue
            if s.level is Level.CRITICAL and s.fatal:
                continue
            if s.in_polling and not s.in_error_path:
                out.append(
                    self._make(
                        FindingType.POLLING_LOGGING,
                        s,
                        title=f"Polling {s.level.value} logs",
                        reason=f"{s.level.value} record emitted on every iteration of a polling loop in `{s.function}`.",
                        recommendation="Keep the severity. Log only when the observed state changes, or every Nth poll "
                        "(e.g. once a minute), and emit a single summary when the poll ends.",
                        fraction=POLLING_SAVINGS,
                        confidence=Confidence.HIGH,
                    )
                )
            elif s.in_loop and not s.in_error_path:
                out.append(
                    self._make(
                        FindingType.LOOP_LOGGING,
                        s,
                        title="Logging inside a loop",
                        reason=f"{s.level.value} record is emitted once per iteration in `{s.function}`; volume scales "
                        "with collection size.",
                        recommendation="Log one summary after the loop (count, duration, failed IDs). "
                        "Do not downgrade to DEBUG automatically - DEBUG may already be off in production.",
                        fraction=LOOP_SAVINGS,
                        confidence=Confidence.MEDIUM,
                    )
                )
            elif s.in_retry:
                out.append(
                    self._make(
                        FindingType.RETRY_LOGGING,
                        s,
                        title="Logging inside a retry block",
                        reason=f"{s.level.value} record is emitted on every attempt of a retry path in `{s.function}`.",
                        recommendation="Log the first failure and the final outcome (attempts, last error) only; "
                        "expected transient retries do not need a per-attempt ERROR.",
                        fraction=RETRY_SAVINGS,
                        confidence=Confidence.MEDIUM,
                    )
                )
        return out

    def consumers(self) -> list[Finding]:
        out = []
        seen_funcs: set[str] = set()
        for s in self.statements:
            if not (s.in_consumer and self._billable(s)) or s.entry_exit or s.in_init or s.in_polling or s.in_loop:
                continue
            if s.level not in (Level.INFO, Level.WARNING) or s.in_error_path or s.in_retry:
                continue
            if s.payload_args:  # already reported as payload logging
                continue
            key = f"{s.file}:{s.function}"
            if key in seen_funcs:
                continue
            seen_funcs.add(key)
            out.append(
                self._make(
                    FindingType.HIGH_FREQUENCY,
                    s,
                    title="High-frequency consumer/scheduler logs",
                    reason=f"`{s.function}` is a consumer/scheduler entry point invoked per message or tick; this "
                    f"{s.level.value} record is emitted for every invocation.",
                    recommendation="Keep entry/exit logs; collapse per-message progress logs into a periodic or per-batch "
                    "summary, or log only outcomes that differ from the normal path.",
                    fraction=CONSUMER_SAVINGS,
                    confidence=Confidence.LOW,
                )
            )
        return out

    # -- Rule 4: low-value logs ------------------------------------------------------------------
    def low_value(self) -> list[Finding]:
        out = []
        for s in self.statements:
            if not self._billable(s) or s.entry_exit or s.in_init or s.level in (Level.CRITICAL, Level.ERROR):
                continue
            msg = s.message.strip()
            if not msg or not _LOW_VALUE.match(msg) or len(s.args) > 1:
                continue
            repeated = self._msg_counts[self._norm(msg)] >= 3
            hot = s.in_loop or s.in_consumer or s.in_polling
            if not (hot or repeated):
                continue
            why = "inside a hot path" if hot else f"repeated {self._msg_counts[self._norm(msg)]}x across the repository"
            out.append(
                self._make(
                    FindingType.LOW_VALUE,
                    s,
                    title="Low-value progress log",
                    reason=f'"{msg}" carries no identifying data and is {why}.',
                    recommendation="Remove it, or merge it into the outcome log with the key identifiers and duration. "
                    "Function entry/exit logs are intentionally not flagged.",
                    fraction=LOW_VALUE_SAVINGS,
                    confidence=Confidence.MEDIUM if hot else Confidence.LOW,
                )
            )
        return out

    # -- Rule 5: incorrect severity ---------------------------------------------------------------
    def severity(self) -> list[Finding]:
        out = []
        retention = self.settings.priority_retention_days
        for s in self.statements:
            if s.level is Level.ERROR:
                reasons = {
                    "validation": ("a validation failure caused by caller input", Level.WARNING),
                    "not_found": ("a resource-not-found condition that is an expected outcome", Level.WARNING),
                    "client_error": ("a client authorisation/authentication error (a caller problem)", Level.WARNING),
                    "recoverable": ("a recoverable/retryable condition", Level.WARNING),
                }
                cond = s.expected_condition
                if not cond and s.in_retry and not s.propagates_error:
                    cond = "recoverable"
                if not cond or s.fatal:
                    continue
                why, target = reasons[cond]
                if cond == "recoverable" and not s.in_retry and not s.in_error_path:
                    continue
                price_ratio = price_per_gb(target, retention) / price_per_gb(Level.ERROR, retention)
                out.append(
                    self._make(
                        FindingType.MISCLASSIFIED_ERROR,
                        s,
                        title="Incorrect ERROR severity",
                        reason=f"Logged at ERROR but this is {why}. ERROR routes to Priority Insights "
                        f"(${price_per_gb(Level.ERROR, retention):.2f}/GB) and can trigger alerts.",
                        recommendation=f"Analyse and log as {target.value} (Store & Search, "
                        f"${price_per_gb(target, retention):.2f}/GB) - or INFO if it must stay alertable. "
                        "Keep ERROR for unexpected failures that need action.",
                        fraction=0.0,
                        price_ratio=price_ratio,
                        confidence=Confidence.HIGH if s.expected_condition else Confidence.MEDIUM,
                    )
                )
            elif s.level is Level.CRITICAL and not s.fatal and not s.in_init:
                hot = s.in_loop or s.in_consumer or s.in_retry or s.in_polling
                if not (hot or s.expected_condition):
                    continue
                target = Level.WARNING if s.expected_condition else Level.ERROR
                price_ratio = price_per_gb(target, retention) / price_per_gb(Level.CRITICAL, retention)
                out.append(
                    self._make(
                        FindingType.MISCLASSIFIED_CRITICAL,
                        s,
                        title="Incorrect CRITICAL severity",
                        reason="CRITICAL is emitted from a repeatedly-invoked or recoverable path and the process does not "
                        "terminate; CRITICAL alerts/pages will fire on a non-fatal condition.",
                        recommendation=f"Reserve CRITICAL for data loss / process-fatal conditions; use {target.value} here.",
                        fraction=0.0,
                        price_ratio=price_ratio,
                        confidence=Confidence.MEDIUM,
                    )
                )
        return out

    # -- Rule 6: duplicate logging ------------------------------------------------------------------
    def duplicates(self) -> list[Finding]:
        out: list[Finding] = []
        reported: set[str] = set()
        for upper in self.statements:
            if not (upper.logs_exception and upper.in_error_path and self._billable(upper)):
                continue
            for callee in upper.guarded_calls:
                if len(callee) < 4:
                    continue
                defs = self._funcs.get(callee, [])
                if len({d.file for d in defs}) != 1:  # ambiguous or unknown callee
                    continue
                for lower in defs:
                    if lower.id in reported or lower.function == upper.function or not self._billable(lower):
                        continue
                    if lower.logs_exception and lower.in_error_path and lower.propagates_error:
                        reported.add(lower.id)
                        out.append(
                            self._make(
                                FindingType.DUPLICATE_LOGGING,
                                lower,
                                title="Duplicate error logging",
                                reason=f"`{lower.function}` logs the error ({lower.location}) and returns/throws it, then the "
                                f"caller `{upper.function}` logs the same error again ({upper.location}).",
                                recommendation="Log the error exactly once, at the boundary that handles it (handler/controller). "
                                "In the lower layer wrap the error with context and propagate it.",
                                fraction=1.0,
                                confidence=Confidence.HIGH,
                                related=[upper.location],
                            )
                        )
        for fn, group in self._funcs.items():
            seen: dict[tuple[str, tuple[str, ...]], LogStatement] = {}
            for s in group:
                if s.in_loop or s.entry_exit or not s.message or not self._billable(s):
                    continue
                key = (self._norm(s.message), tuple(s.args))
                if key in seen and s.id not in reported:
                    reported.add(s.id)
                    first = seen[key]
                    out.append(
                        self._make(
                            FindingType.DUPLICATE_LOGGING,
                            s,
                            title="Duplicate log statement",
                            reason=f"`{fn}` emits the same message with identical arguments at {first.location} and {s.location}.",
                            recommendation="Keep a single statement for this event.",
                            fraction=1.0,
                            confidence=Confidence.MEDIUM,
                            related=[first.location],
                        )
                    )
                else:
                    seen.setdefault(key, s)
        return out

    # -- repeated metadata ------------------------------------------------------------------------------
    def metadata(self) -> list[Finding]:
        out = []
        for s in self.statements:
            if not self._billable(s) or s.in_init or s.payload_args:
                continue
            hits = [a for a in s.args if _METADATA_KEYS.fullmatch(a.split(".")[-1].strip("()"))]
            if len(hits) < 3:
                continue
            out.append(
                self._make(
                    FindingType.REPEATED_METADATA,
                    s,
                    title="Repeated static metadata in log records",
                    reason=f"Static fields ({', '.join(hits[:4])}) are repeated on every record; IBM Cloud Logs already "
                    "attaches applicationName, subsystemName and host metadata.",
                    recommendation="Attach static context once via a child/contextual logger or rely on the "
                    "Logging agent's metadata instead of formatting it into each message.",
                    fraction=METADATA_SAVINGS,
                    confidence=Confidence.MEDIUM,
                )
            )
        return out

    def run(self) -> list[Finding]:
        findings = (
            self.payloads()
            + self.frequency()
            + self.consumers()
            + self.low_value()
            + self.severity()
            + self.duplicates()
            + self.metadata()
        )
        findings.sort(key=lambda f: (-f.est_savings_usd_month, f.file, f.line))
        return findings


def pipeline_label(level: Level) -> Pipeline:
    return pipeline_for(level)
