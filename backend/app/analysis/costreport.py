"""Cost report loader.

Reads billing exports from FINOPS_COST_REPORTS_DIR (``*.json``). Expected shape::

    {
      "period": "2025-06",
      "total_usd_month": 18420.0,
      "services": {
        "schematics-api": {
          "gb_per_day": 310.5, "usd_month": 4600.0,
          "by_severity_gb_day": {"INFO": 220.0, "ERROR": 60.0, "WARNING": 30.5}
        }
      }
    }

The newest report is used to calibrate the static volume model. Raw files are also indexed into the
vector store so the copilot can cite them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.analysis.cost import Calibration
from app.core.pricing import Level

log = logging.getLogger(__name__)


@dataclass
class CostReport:
    period: str
    path: Path
    total_usd_month: float
    services: dict[str, dict] = field(default_factory=dict)
    notes: str = ""

    @property
    def total_gb_day(self) -> float:
        return sum(float(s.get("gb_per_day", 0)) for s in self.services.values())


class CostReports:
    def __init__(self, directory: Path):
        self.directory = directory
        self.reports: list[CostReport] = []
        self.reload()

    def reload(self) -> None:
        reports: list[CostReport] = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    services = raw.get("services", {})
                    total = float(
                        raw.get("total_usd_month") or sum(float(s.get("usd_month", 0)) for s in services.values())
                    )
                    reports.append(
                        CostReport(str(raw.get("period", path.stem)), path, total, services, raw.get("notes", ""))
                    )
                except (OSError, ValueError, TypeError, AttributeError):
                    log.warning("skipping unreadable cost report", extra={"file": path.name})
        self.reports = sorted(reports, key=lambda r: r.period)

    @property
    def latest(self) -> CostReport | None:
        return self.reports[-1] if self.reports else None

    def calibration(self, repository: str) -> Calibration | None:
        report = self.latest
        if report is None or repository not in report.services:
            return None
        svc = report.services[repository]
        by_level: dict[Level, float] = {}
        for key, val in (svc.get("by_severity_gb_day") or {}).items():
            try:
                by_level[Level(key.upper())] = float(val)
            except ValueError:
                continue
        usd = float(svc["usd_month"]) if svc.get("usd_month") else None
        share = round(usd / report.total_usd_month * 100, 1) if usd and report.total_usd_month else None
        return Calibration(
            gb_per_day=float(svc["gb_per_day"]) if svc.get("gb_per_day") else (sum(by_level.values()) or None),
            by_level_gb_day=by_level,
            usd_month=usd,
            platform_share_pct=share,
            period=report.period,
        )

    def trend(self, repository: str) -> list[tuple[str, float]]:
        return [
            (r.period, float(r.services[repository].get("usd_month", 0)))
            for r in self.reports
            if repository in r.services
        ]
