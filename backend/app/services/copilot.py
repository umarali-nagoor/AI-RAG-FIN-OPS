"""Copilot service: RAG-backed root cause analysis, optimisation, PR review and free-form questions.

Every answer is built from facts computed by the analysis engine plus evidence retrieved from the vector store.
The LLM (when configured) writes the narrative; if it is offline or fails, the deterministic report is returned.
"""

from __future__ import annotations

import logging
import re

from app.analysis.pr import PRResult, review_diff
from app.llm.providers import ProviderError
from app.models import (
    AnalyzeResponse,
    AskResponse,
    OptimizeResponse,
    PRFindingOut,
    ReasonOut,
    RecommendationOut,
    RepoAnalysis,
    ReviewPRResponse,
    Source,
)
from app.rag.store import Hit
from app.services import analytics, prompts
from app.services.platform import Platform, RepoState

log = logging.getLogger(__name__)

GUARDRAILS = [
    "Function entry/exit logs are preserved (Rule 1).",
    "INFO is never automatically downgraded to DEBUG - DEBUG is normally disabled in production (Rule 2).",
    "Troubleshooting, monitoring, alerting, security and compliance logs are preserved.",
    "Savings are static-analysis estimates; calibrate with IBM Cloud Logs data usage metrics before committing.",
]


def _pct(v: float) -> str:
    return "<1%" if 0 < v < 1 else f"{v:.0f}%"


def _gb(v: float) -> str:
    return f"{v:+.1f} GB/day" if abs(v) >= 0.05 else ("0 GB/day" if v == 0 else f"{v:+.2f} GB/day")


def _sources(hits: list[Hit]) -> list[Source]:
    out = []
    for h in hits[:10]:
        m = h.metadata
        out.append(
            Source(
                id=h.id,
                type=str(m.get("type", "")),
                title=str(m.get("title", h.id)),
                path=str(m["path"]) + (f":{m['line']}" if m.get("line") else "") if m.get("path") else None,
                score=round(h.score, 3),
                snippet=h.text[:240],
            )
        )
    return out


class Copilot:
    def __init__(self, platform: Platform):
        self.p = platform

    # -- LLM with graceful degradation ------------------------------------------------------------
    def _narrate(self, system: str, user: str) -> str | None:
        if not self.p.llm.available:
            return None
        try:
            text = self.p.llm.complete(system, user)
            return text or None
        except ProviderError as exc:
            log.warning("llm call failed, using deterministic report", extra={"error": str(exc)})
            return None

    @property
    def provider(self) -> str:
        return self.p.llm.name

    # ======================================================================================
    # Reactive analysis
    # ======================================================================================
    def analyze(self, repository: str, question: str | None = None) -> AnalyzeResponse:
        st = self.p.state(repository)
        a = st.analysis
        question = question or f"Why is {repository} costing more?"
        hits = self.p.retriever.context(question + " " + " ".join(r.reason for r in a.top_reasons[:3]), repository)
        facts = self._facts(a)
        narrative = self._narrate(prompts.SYSTEM, prompts.root_cause_prompt(question, facts, hits))
        files = self._top_files(a)
        report = self._root_cause_report(a, st, narrative)
        return AnalyzeResponse(
            repository=repository,
            riskLevel=a.risk_level,
            topReasons=[ReasonOut(reason=r.reason, impact=r.impact) for r in a.top_reasons[:5]],
            estimatedOptimizationPotential=_pct(a.potential_pct),
            optimizationRange=f"{a.potential_low_pct:.0f}-{a.potential_high_pct:.0f}%",
            platformCostShare=f"{a.cost.platform_share_pct:.0f}%" if a.cost.platform_share_pct is not None else None,
            costBasis=a.cost.basis,
            estimatedGbPerDay=a.cost.gb_per_day,
            estimatedUsdPerMonth=a.cost.usd_per_month,
            filesIdentified=files,
            rootCause=report,
            sources=_sources(hits),
            llmProvider=self.provider,
        )

    def _top_files(self, a: RepoAnalysis, n: int = 6) -> list[str]:
        weight: dict[str, float] = {}
        for f in a.findings:
            weight[f.file] = weight.get(f.file, 0.0) + f.est_savings_usd_month
        return [f for f, _ in sorted(weight.items(), key=lambda kv: -kv[1])[:n]]

    def _facts(self, a: RepoAnalysis) -> str:
        lines = [
            f"repository={a.repository}; risk={a.risk_level.value}; cost={a.cost.gb_per_day} GB/day "
            f"${a.cost.usd_per_month:,.0f}/month ({a.cost.basis}); platform share="
            f"{a.cost.platform_share_pct if a.cost.platform_share_pct is not None else 'unknown'}%; "
            f"optimisation potential {a.potential_low_pct:.0f}-{a.potential_high_pct:.0f}%",
            f"volume by severity (GB/day): {a.cost.by_level_gb_day}; spend by pipeline (USD/month): {a.cost.by_pipeline_usd_month}",
            "top reasons:",
        ]
        for r in a.top_reasons[:6]:
            lines.append(
                f"- {r.reason}: impact={r.impact.value}, sites={r.count}, ~{r.share_pct}% of cost, files={r.files[:3]}"
            )
        lines.append("top findings:")
        for f in a.findings[:8]:
            lines.append(f"- {f.title} @ {f.file}:{f.line} ({f.level.value}) ~{f.est_savings_pct}% :: {f.reason}")
        return "\n".join(lines)

    def _root_cause_report(self, a: RepoAnalysis, st: RepoState, narrative: str | None) -> str:
        cost = a.cost
        share = (
            f"approximately {cost.platform_share_pct:.0f}% of platform logging cost"
            if cost.platform_share_pct is not None
            else "an estimated share of platform logging cost"
        )
        head = (
            f"{a.repository} contributes {share} (${cost.usd_per_month:,.0f}/month, {cost.gb_per_day:,.1f} GB/day, "
            f"{cost.basis})."
        )
        if narrative:
            head += f"\n\n{narrative.strip()}"
        else:
            head += f" Risk level: {a.risk_level.value}."
        lines = [head, "", "Primary contributors:"]
        if not a.top_reasons:
            lines.append("No costly logging patterns were detected.")
        for i, r in enumerate(a.top_reasons[:5], 1):
            lines.append(f"{i}. {r.reason} - {r.impact.value} impact, {r.count} site(s), ~{r.share_pct:.0f}% of cost")
        files = self._top_files(a)
        if files:
            lines += ["", "Files identified:"] + [f"- {f}" for f in files]
        lines += [
            "",
            f"Potential optimisation: {a.potential_low_pct:.0f}-{a.potential_high_pct:.0f}% "
            f"(≈ ${a.metrics['potential_usd_month']:,.0f}/month before confidence weighting)",
        ]
        recent = [h for h in st.history if h.findings and h.gb_day > 0][:2]
        if recent:
            lines += ["", "Recently introduced:"] + [
                f"- {h.sha} ({h.date}) {h.subject}: {_gb(h.gb_day)}" for h in recent
            ]
        return "\n".join(lines)

    # ======================================================================================
    # Optimisation
    # ======================================================================================
    def optimize(self, repository: str, question: str | None = None) -> OptimizeResponse:
        st = self.p.state(repository)
        a = st.analysis
        question = question or f"How can I reduce {repository} IBM Cloud Logs costs?"
        recs: list[RecommendationOut] = []
        seen: set[str] = set()
        for f in a.findings:
            key = f"{f.file}:{f.line}:{f.type}"
            if key in seen or (f.est_savings_pct <= 0 and f.est_savings_usd_month <= 0):
                continue
            seen.add(key)
            recs.append(
                RecommendationOut(
                    issue=f.title,
                    location=f"{f.file}:{f.line}",
                    recommendation=f.recommendation,
                    estimatedSavings=_pct(f.est_savings_pct),
                    impact=f.impact,
                    confidence=f.confidence,
                    rule=f.rule,
                    currentCode=f.snippet or None,
                    suggestedCode=f.suggested_code,
                    estimatedUsdPerMonthSaved=f.est_savings_usd_month,
                )
            )
        recs = recs[:25]
        hits = self.p.retriever.context(question, repository)
        facts = self._facts(a)
        narrative = self._narrate(prompts.SYSTEM, prompts.optimize_prompt(question, facts, hits))
        if not narrative:
            top = ", ".join(sorted({r.issue for r in recs[:5]}))
            narrative = (
                f"{len(recs)} safe optimisations found for {repository}. Start with: {top or 'none'}. "
                f"Combined potential {a.potential_low_pct:.0f}-{a.potential_high_pct:.0f}% of logging cost."
            )
        return OptimizeResponse(
            repository=repository,
            recommendations=recs,
            estimatedOptimizationPotential=_pct(a.potential_pct),
            guardrails=GUARDRAILS,
            summary=narrative,
            sources=_sources(hits),
            llmProvider=self.provider,
        )

    # ======================================================================================
    # Proactive PR review
    # ======================================================================================
    def review_pr(self, repository: str, diff: str) -> ReviewPRResponse:
        st = self.p.state(repository)
        res = review_diff(
            self.p.settings, repository, diff, st.analysis.statements, self.p.reports.calibration(repository), st.root
        )
        return self._pr_response(repository, res)

    def _pr_response(self, repository: str, res: PRResult, extra_hits: list[Hit] | None = None) -> ReviewPRResponse:
        query = "pull request logging cost " + " ".join(f.type for f in res.findings[:4])
        hits = extra_hits or self.p.retriever.context(query, repository)
        findings = [
            PRFindingOut(
                type=f.type,
                reason=f.reason,
                location=f.location,
                level=f.level.value,
                impact=f.impact,
                estimatedGbPerDay=round(f.gb_day, 3),
                recommendation=f.recommendation,
                suggestedCode=f.suggested_code,
            )
            for f in res.findings
        ]
        facts = (
            f"repository={repository}; risk={res.risk.value}; decision={res.decision}; net={_gb(res.net_gb_day)} "
            f"(added {res.added_gb_day} / removed {res.removed_gb_day}); growth={res.growth_pct}%; "
            f"${res.net_usd_month:,.0f}/month; new log statements={len(res.new_statements)}\n"
            + "\n".join(
                f"- {f.type} @ {f.location} ({f.level.value}) {f.gb_day:.2f} GB/day: {f.reason}" for f in res.findings
            )
        )
        narrative = self._narrate(prompts.SYSTEM, prompts.pr_prompt(facts, hits)) if res.findings else None
        if res.findings:
            rec = narrative or " ".join(dict.fromkeys(f.recommendation.rstrip(".") + "." for f in res.findings[:3]))
            summary = (
                f"{len(res.findings)} issue(s) found; net {_gb(res.net_gb_day)} (≈ ${res.net_usd_month:,.0f}/month)."
            )
        else:
            rec = "No costly logging patterns detected. Safe to merge from an IBM Cloud Logs cost perspective."
            summary = (
                f"No issues found; net {_gb(res.net_gb_day)}."
                if res.files
                else "The diff contains no Go, Java or Python changes to review."
            )
        return ReviewPRResponse(
            repository=repository,
            risk=res.risk,
            decision=res.decision,
            estimatedCostImpact=_gb(res.net_gb_day),
            estimatedUsdPerMonth=res.net_usd_month,
            findings=findings,
            recommendation=rec,
            summary=summary,
            newLogStatements=len(res.new_statements),
            removedLogStatements=res.removed_statements,
            sources=_sources(hits),
            llmProvider=self.provider,
        )

    # ======================================================================================
    # Free-form questions (routes to the right capability)
    # ======================================================================================
    def ask(self, question: str, repository: str | None = None, diff: str | None = None) -> AskResponse:
        repo = repository or self._mentioned_repo(question)
        intent = self._intent(question, repo, diff)

        if intent == "pr_review" and repo and diff:
            r = self.review_pr(repo, diff)
            text = f"Risk {r.risk.value} ({r.decision}); estimated impact {r.estimatedCostImpact}.\n{r.recommendation}"
            for f in r.findings[:6]:
                text += f"\n- {f.type} at {f.location}: {f.reason}"
            return AskResponse(
                answer=text,
                intent=intent,
                repository=repo,
                data=r.model_dump(mode="json"),
                sources=r.sources,
                llmProvider=self.provider,
            )
        if intent == "pr_review":
            return AskResponse(
                answer="To assess a PR's IBM Cloud Logs cost impact, select a repository and provide its unified diff "
                "(`git diff`).",
                intent=intent,
                repository=repo,
                llmProvider=self.provider,
                grounded=False,
            )
        if intent == "platform":
            states = self.p.all_states()
            text, data = analytics.answer(question, states)
            hits = self.p.retriever.context(question, None, 3)
            narrative = self._narrate(prompts.SYSTEM, prompts.ask_prompt(question, text, hits))
            return AskResponse(
                answer=text + (f"\n\n{narrative}" if narrative else ""),
                intent=intent,
                data=data,
                sources=_sources(hits),
                llmProvider=self.provider,
            )
        if intent == "optimize" and repo:
            o = self.optimize(repo, question)
            lines = [o.summary, ""]
            for r in o.recommendations[:8]:
                lines.append(f"- {r.issue} @ {r.location}: {r.recommendation} (≈ {r.estimatedSavings})")
            return AskResponse(
                answer="\n".join(lines),
                intent=intent,
                repository=repo,
                data=o.model_dump(mode="json"),
                sources=o.sources,
                llmProvider=self.provider,
            )
        if repo:
            r = self.analyze(repo, question)
            return AskResponse(
                answer=r.rootCause,
                intent="root_cause",
                repository=repo,
                data=r.model_dump(mode="json"),
                sources=r.sources,
                llmProvider=self.provider,
            )

        hits = self.p.retriever.context(question, None, 4)
        guidance = [h for h in hits if h.metadata.get("type") in {"standard", "ibm_guidance", "incident"}]
        facts = "\n".join(f"[{h.metadata.get('title')}] {h.text[:500]}" for h in guidance[:4])
        narrative = self._narrate(
            prompts.SYSTEM, prompts.ask_prompt(question, facts or "(no repository specified)", hits)
        )
        if narrative:
            return AskResponse(answer=narrative, intent="guidance", sources=_sources(hits), llmProvider=self.provider)
        if guidance:
            body = "\n\n".join(f"{h.metadata.get('title')}:\n{h.text[:600]}" for h in guidance[:3])
            return AskResponse(
                answer=f"Most relevant guidance from the knowledge base:\n\n{body}",
                intent="guidance",
                sources=_sources(hits),
                llmProvider=self.provider,
            )
        return AskResponse(
            answer="I could not find grounded information for that. Name a repository or ask a platform-wide question.",
            intent="guidance",
            sources=[],
            llmProvider=self.provider,
            grounded=False,
        )

    def _mentioned_repo(self, question: str) -> str | None:
        q = question.lower()
        found = [n for n in self.p.registry.names() if n.lower() in q]
        return max(found, key=len) if found else None  # longest match: "schematics-agents-api" over "schematics-api"

    @staticmethod
    def _intent(question: str, repo: str | None, diff: str | None) -> str:
        q = question.lower()
        if diff or re.search(r"\b(pr|pull request|diff|merge)\b", q):
            return "pr_review"
        cross = r"\b(repositories|repos|services|platform|rank|compare)\b"
        which = r"\b(which|highest|most|top)\b"
        if (repo is None and (re.search(cross, q) or re.search(which, q))) or (
            repo and re.search(r"\b(rank|compare)\b", q)
        ):
            return "platform"
        if re.search(r"\b(reduce|optimi[sz]e|lower|cut|save|savings|fix|recommend|improve)\b", q):
            return "optimize"
        return "root_cause"
