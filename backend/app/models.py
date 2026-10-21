"""Domain and API models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.pricing import Level


class Impact(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


CONFIDENCE_WEIGHT = {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.6, Confidence.LOW: 0.3}


class PayloadKind(StrEnum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    PAYLOAD = "PAYLOAD"
    OBJECT = "OBJECT"
    COLLECTION = "COLLECTION"
    JSON_BLOB = "JSON_BLOB"


class FindingType(StrEnum):
    REQUEST_PAYLOAD = "REQUEST_PAYLOAD"
    RESPONSE_PAYLOAD = "RESPONSE_PAYLOAD"
    OBJECT_DUMP = "OBJECT_DUMP"
    JSON_BLOB = "JSON_BLOB"
    LOOP_LOGGING = "LOOP_LOGGING"
    RETRY_LOGGING = "RETRY_LOGGING"
    POLLING_LOGGING = "POLLING_LOGGING"
    HIGH_FREQUENCY = "HIGH_FREQUENCY"
    LOW_VALUE = "LOW_VALUE"
    MISCLASSIFIED_ERROR = "MISCLASSIFIED_ERROR"
    MISCLASSIFIED_CRITICAL = "MISCLASSIFIED_CRITICAL"
    DUPLICATE_LOGGING = "DUPLICATE_LOGGING"
    REPEATED_METADATA = "REPEATED_METADATA"


PAYLOAD_FINDING_TYPES = {
    FindingType.REQUEST_PAYLOAD,
    FindingType.RESPONSE_PAYLOAD,
    FindingType.OBJECT_DUMP,
    FindingType.JSON_BLOB,
}
FREQUENCY_FINDING_TYPES = {
    FindingType.LOOP_LOGGING,
    FindingType.RETRY_LOGGING,
    FindingType.POLLING_LOGGING,
    FindingType.HIGH_FREQUENCY,
}


class PayloadArg(BaseModel):
    expr: str
    kind: PayloadKind
    confidence: Confidence = Confidence.MEDIUM


class LogStatement(BaseModel):
    """One logging call found in source code, with the structural context it executes in."""

    id: str
    repository: str
    file: str
    line: int
    end_line: int
    language: str
    level: Level
    logger_call: str
    message: str = ""
    args: list[str] = Field(default_factory=list)
    function: str | None = None
    in_loop: bool = False
    in_retry: bool = False
    in_polling: bool = False
    in_consumer: bool = False  # message consumers, schedulers, controllers (invoked repeatedly)
    in_error_path: bool = False
    in_init: bool = False
    guard: str = ""  # catch types / `if err != nil` condition text for error-path logs
    expected_condition: str = ""  # validation | not_found | client_error | recoverable (error-path context)
    guarded_calls: list[str] = Field(default_factory=list)  # callees whose failure leads to this error log
    propagates_error: bool = False
    logs_exception: bool = False  # passes an exception/stack trace to the logger
    fatal: bool = False  # process exits (log.Fatal / panic): legitimately CRITICAL
    payload_args: list[PayloadArg] = Field(default_factory=list)
    entry_exit: bool = False
    snippet: str = ""
    is_new: bool = False  # set by PR review for added lines

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}"


class Finding(BaseModel):
    id: str
    type: FindingType
    rule: int  # optimisation rule number 3..8 (see knowledge/optimization-standards.md)
    title: str
    impact: Impact = Impact.LOW
    confidence: Confidence = Confidence.MEDIUM
    repository: str
    file: str
    line: int
    function: str | None = None
    level: Level
    snippet: str = ""
    reason: str
    recommendation: str
    suggested_code: str | None = None
    related: list[str] = Field(default_factory=list)
    est_savings_pct: float = 0.0  # % of the repository's logging cost
    est_savings_usd_month: float = 0.0
    est_gb_day_saved: float = 0.0


class RepoCostSummary(BaseModel):
    basis: str  # "calibrated" (cost report) or "estimated" (static model)
    gb_per_day: float
    usd_per_month: float
    platform_share_pct: float | None = None
    period: str | None = None
    by_level_gb_day: dict[str, float] = Field(default_factory=dict)
    by_pipeline_usd_month: dict[str, float] = Field(default_factory=dict)


class RepoAnalysis(BaseModel):
    repository: str
    commit: str | None = None
    languages: list[str] = Field(default_factory=list)
    files_scanned: int = 0
    statements: list[LogStatement] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    cost: RepoCostSummary
    risk_level: Impact
    potential_pct: float = 0.0
    potential_low_pct: float = 0.0
    potential_high_pct: float = 0.0
    top_reasons: list[TopReason] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class TopReason(BaseModel):
    reason: str
    impact: Impact
    count: int = 0
    share_pct: float = 0.0
    files: list[str] = Field(default_factory=list)


RepoAnalysis.model_rebuild()


# --------------------------------------------------------------------------------------------
# API schemas
# --------------------------------------------------------------------------------------------
class RepoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ReviewPRRequest(RepoRequest):
    diff: str = Field(min_length=1)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3)
    repository: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    diff: str | None = None


class ReasonOut(BaseModel):
    reason: str
    impact: Impact


class Source(BaseModel):
    id: str
    type: str
    title: str
    path: str | None = None
    score: float | None = None
    snippet: str = ""


class AnalyzeResponse(BaseModel):
    repository: str
    riskLevel: Impact  # noqa: N815 - public API contract
    topReasons: list[ReasonOut]  # noqa: N815
    estimatedOptimizationPotential: str  # noqa: N815
    optimizationRange: str  # noqa: N815
    platformCostShare: str | None = None  # noqa: N815
    costBasis: str  # noqa: N815
    estimatedGbPerDay: float  # noqa: N815
    estimatedUsdPerMonth: float  # noqa: N815
    filesIdentified: list[str]  # noqa: N815
    rootCause: str  # noqa: N815
    sources: list[Source] = Field(default_factory=list)
    llmProvider: str  # noqa: N815


class RecommendationOut(BaseModel):
    issue: str
    location: str
    recommendation: str
    estimatedSavings: str  # noqa: N815
    impact: Impact
    confidence: Confidence
    rule: int
    currentCode: str | None = None  # noqa: N815
    suggestedCode: str | None = None  # noqa: N815
    estimatedUsdPerMonthSaved: float = 0.0  # noqa: N815


class OptimizeResponse(BaseModel):
    repository: str
    recommendations: list[RecommendationOut]
    estimatedOptimizationPotential: str  # noqa: N815
    guardrails: list[str]
    summary: str
    sources: list[Source] = Field(default_factory=list)
    llmProvider: str  # noqa: N815


class PRFindingOut(BaseModel):
    type: str
    reason: str
    location: str
    level: str
    impact: Impact
    estimatedGbPerDay: float  # noqa: N815
    recommendation: str
    suggestedCode: str | None = None  # noqa: N815


class ReviewPRResponse(BaseModel):
    repository: str
    risk: Impact
    decision: str  # PASS | WARN | BLOCK
    estimatedCostImpact: str  # noqa: N815
    estimatedUsdPerMonth: float  # noqa: N815
    findings: list[PRFindingOut]
    recommendation: str
    summary: str
    newLogStatements: int  # noqa: N815
    removedLogStatements: int  # noqa: N815
    sources: list[Source] = Field(default_factory=list)
    llmProvider: str  # noqa: N815


class AskResponse(BaseModel):
    answer: str
    intent: str
    repository: str | None = None
    data: dict[str, Any] | None = None
    sources: list[Source] = Field(default_factory=list)
    llmProvider: str  # noqa: N815
    grounded: bool = True


class PRSummaryOut(BaseModel):
    number: int
    title: str
    author: str
    branch: str
    base: str
    state: str
    url: str
    createdAt: str   # noqa: N815
    updatedAt: str   # noqa: N815


class RepositoryInfo(BaseModel):
    name: str
    description: str = ""
    languages: list[str] = Field(default_factory=list)
    available: bool
    indexed: bool = False
    commit: str | None = None
