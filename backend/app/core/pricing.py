"""IBM Cloud Logs pricing model.

Prices are fetched live from the IBM Global Catalog API (no auth required):
  https://globalcatalog.cloud.ibm.com/api/v1/3373c949-d1b1-4935-a60a-4e80b15440dd/pricing

The catalog is queried once at startup and cached for CACHE_TTL_S seconds.
If the catalog is unreachable the last known good values are used as fallback.

Severity → pipeline routing follows the TCO policy:
  CRITICAL, ERROR   → Priority insights  (high)
  INFO, DEBUG       → Analyze and alert  (medium)
  WARNING, VERBOSE  → Store and search   (low)
"""

from __future__ import annotations

import logging
import threading
import time
from enum import StrEnum

import httpx

log = logging.getLogger(__name__)

# IBM Global Catalog pricing endpoint for IBM Cloud Logs Standard plan.
# Public — no authentication required.
_CATALOG_PRICING_URL = (
    "https://globalcatalog.cloud.ibm.com/api/v1"
    "/3373c949-d1b1-4935-a60a-4e80b15440dd/pricing"
)

# Fallback prices (USD/GB) — used when the catalog is unreachable.
# Keep these in sync with the published pricing page as a last resort.
_FALLBACK_STORE_AND_SEARCH = 0.4848
_FALLBACK_ANALYZE_AND_ALERT = 0.76
_FALLBACK_PRIORITY_INSIGHTS: dict[int, float] = {
    7: 1.212,
    14: 1.77,
    30: 2.27,
    60: 2.78,
    90: 3.28,
}

# Metric IDs returned by the Global Catalog API
_METRIC_STORE = "STORE-AND-SEARCH"
_METRIC_ANALYZE = "ANALYZE-AND-ALERT"
_METRIC_PRIORITY_PREFIX = "PRIORITY-INSIGHTS-RETENTION-"
_RETENTION_DAYS = [7, 14, 30, 60, 90]

CACHE_TTL_S = 3600  # re-fetch prices every hour


class Level(StrEnum):
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"
    VERBOSE = "VERBOSE"


class Pipeline(StrEnum):
    PRIORITY_INSIGHTS = "Priority Insights"
    ANALYZE_AND_ALERT = "Analyze & Alert"
    STORE_AND_SEARCH = "Store & Search"


LEVEL_PIPELINE: dict[Level, Pipeline] = {
    Level.CRITICAL: Pipeline.PRIORITY_INSIGHTS,
    Level.ERROR: Pipeline.PRIORITY_INSIGHTS,
    Level.INFO: Pipeline.ANALYZE_AND_ALERT,
    Level.DEBUG: Pipeline.ANALYZE_AND_ALERT,
    Level.WARNING: Pipeline.STORE_AND_SEARCH,
    Level.VERBOSE: Pipeline.STORE_AND_SEARCH,
}

PIPELINES_WITH_ALERTING = {Pipeline.PRIORITY_INSIGHTS, Pipeline.ANALYZE_AND_ALERT}


# ---------------------------------------------------------------------------
# Live price cache
# ---------------------------------------------------------------------------

class _PriceCache:
    """Thread-safe cache for IBM Cloud Logs prices fetched from the catalog."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store_and_search: float = _FALLBACK_STORE_AND_SEARCH
        self._analyze_and_alert: float = _FALLBACK_ANALYZE_AND_ALERT
        self._priority_insights: dict[int, float] = dict(_FALLBACK_PRIORITY_INSIGHTS)
        self._fetched_at: float = 0.0
        self._effective_from: str = "fallback"
        self._source: str = "fallback"

    def _fetch(self) -> None:
        """Fetch prices from the IBM Global Catalog. Called with lock held."""
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(_CATALOG_PRICING_URL, headers={"Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("IBM catalog price fetch failed — using cached/fallback values", extra={"error": str(exc)})
            return

        store: float | None = None
        analyze: float | None = None
        priority: dict[int, float] = {}

        for metric in data.get("metrics", []):
            mid = metric.get("metric_id", "")
            # Extract USD price
            usd = next(
                (a for a in metric.get("amounts", []) if a.get("country") == "USA"),
                None,
            )
            if not usd:
                continue
            price_entry = usd.get("prices", [{}])[0]
            price = float(price_entry.get("price", 0))
            if price <= 0:
                continue

            if mid == _METRIC_STORE:
                store = price
            elif mid == _METRIC_ANALYZE:
                analyze = price
            elif mid.startswith(_METRIC_PRIORITY_PREFIX):
                # e.g. PRIORITY-INSIGHTS-RETENTION-7-DAYS → 7
                suffix = mid[len(_METRIC_PRIORITY_PREFIX):]  # "7-DAYS"
                try:
                    days = int(suffix.split("-")[0])
                    if days in _RETENTION_DAYS:
                        priority[days] = price
                except ValueError:
                    continue

        if store is not None:
            self._store_and_search = store
        if analyze is not None:
            self._analyze_and_alert = analyze
        if priority:
            self._priority_insights.update(priority)

        self._fetched_at = time.monotonic()
        self._effective_from = data.get("effective_from", "unknown")
        self._source = "IBM Global Catalog"
        log.info(
            "IBM Cloud Logs prices refreshed",
            extra={
                "effective_from": self._effective_from,
                "store_and_search": self._store_and_search,
                "analyze_and_alert": self._analyze_and_alert,
                "priority_insights": self._priority_insights,
            },
        )

    def _maybe_refresh(self) -> None:
        if time.monotonic() - self._fetched_at > CACHE_TTL_S:
            self._fetch()

    def store_and_search(self) -> float:
        with self._lock:
            self._maybe_refresh()
            return self._store_and_search

    def analyze_and_alert(self) -> float:
        with self._lock:
            self._maybe_refresh()
            return self._analyze_and_alert

    def priority_insights(self, retention_days: int) -> float:
        with self._lock:
            self._maybe_refresh()
            return self._priority_insights.get(retention_days, self._priority_insights[7])

    def source(self) -> str:
        with self._lock:
            return f"{self._source} (effective {self._effective_from})"

    def all_prices(self) -> dict:
        with self._lock:
            self._maybe_refresh()
            return {
                "source": self._source,
                "effective_from": self._effective_from,
                "store_and_search_usd_gb": self._store_and_search,
                "analyze_and_alert_usd_gb": self._analyze_and_alert,
                "priority_insights_usd_gb": dict(self._priority_insights),
            }


# Module-level singleton
_cache = _PriceCache()


def warm_price_cache() -> None:
    """Fetch prices at startup so the first request is never slow."""
    _cache._maybe_refresh()  # noqa: SLF001


# ---------------------------------------------------------------------------
# Public API — same interface as before, now live
# ---------------------------------------------------------------------------

def to_level(value: str) -> Level:
    return Level(value.upper())


def pipeline_for(level: Level | str) -> Pipeline:
    return LEVEL_PIPELINE[Level(level)]


def price_per_gb(level: Level | str, retention_days: int = 7) -> float:
    """USD per GB ingested for a severity, using live IBM Global Catalog prices."""
    pipeline = pipeline_for(level)
    if pipeline is Pipeline.PRIORITY_INSIGHTS:
        return _cache.priority_insights(retention_days)
    if pipeline is Pipeline.ANALYZE_AND_ALERT:
        return _cache.analyze_and_alert()
    return _cache.store_and_search()


def current_prices() -> dict:
    """Return all current prices — used by the /pricing endpoint."""
    return _cache.all_prices()
