"""Volume and cost model.

Static analysis cannot see runtime volume, so each statement gets an *estimated* daily event count
derived from the structural context it runs in, and an estimated byte size derived from what it logs.
When a cost report is available the estimates are calibrated per severity so that the repository total
matches the real GB/day from billing (basis = "calibrated"); otherwise results are labelled "estimated".

Rule 2 is built in: DEBUG/VERBOSE statements are assumed disabled in production and cost nothing unless
FINOPS_DEBUG_ENABLED_IN_PROD=true.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.pricing import Level, pipeline_for, price_per_gb
from app.models import Confidence, LogStatement, PayloadKind, RepoCostSummary

BYTES_PER_GB = 1_000_000_000
DAYS_PER_MONTH = 30

# --- tunable assumptions (documented in docs/COST_MODEL.md) -------------------------------------
BASE_EVENTS_PER_DAY = 2_000
INIT_EVENTS_PER_DAY = 10
ERROR_PATH_EVENTS_PER_DAY = 200
CRITICAL_EVENTS_PER_DAY = 20
CONSUMER_MULTIPLIER = 5
LOOP_MULTIPLIER = 15
RETRY_MULTIPLIER = 2
POLLING_EVENTS_PER_DAY = 20_000  # one log every ~4s

ENVELOPE_BYTES = 350  # app/subsystem/severity/timestamp/host metadata added to every record
BYTES_PER_ARG = 24
EXCEPTION_BYTES = 800
PAYLOAD_BYTES: dict[PayloadKind, int] = {
    PayloadKind.REQUEST: 2_500,
    PayloadKind.RESPONSE: 4_000,
    PayloadKind.PAYLOAD: 3_000,
    PayloadKind.OBJECT: 1_800,
    PayloadKind.COLLECTION: 4_000,
    PayloadKind.JSON_BLOB: 3_500,
}
_PAYLOAD_CONF = {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.7, Confidence.LOW: 0.4}
ID_REPLACEMENT_BYTES = 40  # what a requestId/workspaceId field costs


def events_per_day(s: LogStatement) -> float:
    if s.in_init and not (s.in_loop or s.in_polling):
        return INIT_EVENTS_PER_DAY
    if s.level is Level.CRITICAL:
        return CRITICAL_EVENTS_PER_DAY
    ev = ERROR_PATH_EVENTS_PER_DAY if s.in_error_path else BASE_EVENTS_PER_DAY
    if s.in_consumer:
        ev *= CONSUMER_MULTIPLIER
    if s.in_polling and not s.in_error_path:  # error-path logs fire on failures, not on every tick
        return max(ev, POLLING_EVENTS_PER_DAY)
    if s.in_loop:
        ev *= LOOP_MULTIPLIER
    if s.in_retry:
        ev *= RETRY_MULTIPLIER
    return ev


def payload_bytes(s: LogStatement) -> float:
    return sum(PAYLOAD_BYTES[p.kind] * _PAYLOAD_CONF[p.confidence] for p in s.payload_args)


def event_bytes(s: LogStatement) -> float:
    base = ENVELOPE_BYTES + len(s.message) + BYTES_PER_ARG * len(s.args)
    if s.logs_exception:
        base += EXCEPTION_BYTES
    return base + payload_bytes(s)


@dataclass
class Calibration:
    gb_per_day: float | None = None
    by_level_gb_day: dict[Level, float] = field(default_factory=dict)
    usd_month: float | None = None
    platform_share_pct: float | None = None
    period: str | None = None


class CostModel:
    def __init__(self, settings: Settings, statements: list[LogStatement], calibration: Calibration | None = None):
        self.settings = settings
        self.calibration = calibration
        self._scale: dict[Level, float] = {}
        self._fit(statements)

    # -- calibration ----------------------------------------------------------------------------
    def _active(self, level: Level) -> bool:
        if level in (Level.DEBUG, Level.VERBOSE):
            return self.settings.debug_enabled_in_prod
        return True

    def _raw_gb_day(self, s: LogStatement) -> float:
        if not self._active(s.level):
            return 0.0
        return events_per_day(s) * event_bytes(s) / BYTES_PER_GB

    def _fit(self, statements: list[LogStatement]) -> None:
        cal = self.calibration
        est: dict[Level, float] = {lv: 0.0 for lv in Level}
        for s in statements:
            est[s.level] += self._raw_gb_day(s)
        self._est_total = sum(est.values())
        if cal is None or not (cal.gb_per_day or cal.by_level_gb_day):
            self._scale = {lv: 1.0 for lv in Level}
            return
        if cal.by_level_gb_day:
            scales = {lv: (gb / est[lv]) for lv, gb in cal.by_level_gb_day.items() if est[lv] > 0}
            fallback = sum(scales.values()) / len(scales) if scales else 1.0
            for lv in Level:
                if lv in scales:
                    self._scale[lv] = scales[lv]
                elif not self._active(lv) or (lv in cal.by_level_gb_day and est[lv] == 0):
                    self._scale[lv] = 1.0
                else:
                    self._scale[lv] = fallback
        else:
            k = cal.gb_per_day / self._est_total if self._est_total > 0 and cal.gb_per_day else 1.0
            self._scale = {lv: k for lv in Level}

    @property
    def basis(self) -> str:
        return (
            "calibrated"
            if self.calibration and (self.calibration.gb_per_day or self.calibration.by_level_gb_day)
            else "estimated"
        )

    # -- per statement --------------------------------------------------------------------------
    def gb_day(self, s: LogStatement) -> float:
        return self._raw_gb_day(s) * self._scale.get(s.level, 1.0)

    def price(self, level: Level) -> float:
        return price_per_gb(level, self.settings.priority_retention_days)

    def usd_month(self, s: LogStatement) -> float:
        return self.gb_day(s) * self.price(s.level) * DAYS_PER_MONTH

    def payload_fraction(self, s: LogStatement) -> float:
        """Fraction of the event's bytes that are whole-object payload (saved when replaced with an ID)."""
        total = event_bytes(s)
        pb = payload_bytes(s)
        if pb <= 0 or total <= 0:
            return 0.0
        replaced = ID_REPLACEMENT_BYTES * len(s.payload_args)
        return max(0.0, min(0.97, (pb - replaced) / total))

    # -- aggregate ------------------------------------------------------------------------------
    def summarize(self, statements: list[LogStatement]) -> RepoCostSummary:
        by_level: dict[str, float] = {}
        by_pipe: dict[str, float] = {}
        total_gb = 0.0
        total_usd = 0.0
        for s in statements:
            gb = self.gb_day(s)
            usd = gb * self.price(s.level) * DAYS_PER_MONTH
            by_level[s.level.value] = by_level.get(s.level.value, 0.0) + gb
            pipe = pipeline_for(s.level).value
            by_pipe[pipe] = by_pipe.get(pipe, 0.0) + usd
            total_gb += gb
            total_usd += usd
        cal = self.calibration
        if cal and cal.usd_month:
            total_usd = cal.usd_month
        return RepoCostSummary(
            basis=self.basis,
            gb_per_day=round(total_gb, 3),
            usd_per_month=round(total_usd, 2),
            platform_share_pct=cal.platform_share_pct if cal else None,
            period=cal.period if cal else None,
            by_level_gb_day={k: round(v, 3) for k, v in sorted(by_level.items())},
            by_pipeline_usd_month={k: round(v, 2) for k, v in sorted(by_pipe.items())},
        )
