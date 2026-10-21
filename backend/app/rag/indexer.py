"""Knowledge base indexing and retrieval.

Document types stored in the vector DB (metadata ``type``):
  log_statement  one per extracted logging call, with structural context
  finding        one per detector finding
  pattern        repository-level logging pattern summary
  readme         README / architecture / runbook docs from the repositories
  standard       logging & optimisation standards (knowledge/standards)
  ibm_guidance   IBM Cloud Logs pricing / TCO / cost-control guidance (knowledge/ibm)
  cost_report    billing exports (per service + platform summary)
  incident       historical incidents / past findings (knowledge/incidents)
  pr_history     recent commits with their logging impact

Repository-scoped documents carry ``repository=<name>``; global documents carry ``repository=*``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from app.analysis.costreport import CostReports
from app.ingest.repos import CommitInfo
from app.llm.providers import Embedder
from app.models import RepoAnalysis
from app.rag.chunking import chunk_markdown
from app.rag.store import Doc, Hit, VectorStore

log = logging.getLogger(__name__)

GLOBAL = "*"
KNOWLEDGE_TYPES = {"standards": "standard", "ibm": "ibm_guidance", "incidents": "incident", "findings": "incident"}
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]  # noqa: S324


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end() :]


# ---------------------------------------------------------------------------------------------
class KnowledgeBase:
    def __init__(self, store: VectorStore, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    # -- low level -------------------------------------------------------------------------------
    def _put(self, docs: list[Doc]) -> int:
        if not docs:
            return 0
        self.store.upsert(docs, self.embedder.embed_documents([d.text for d in docs]))
        return len(docs)

    def stats(self) -> dict[str, int]:
        return {"documents": self.store.count()}

    def indexed_signature(self, repository: str) -> str | None:
        docs = self.store.get({"$and": [{"repository": repository}, {"type": "pattern"}]}, limit=1)
        return str(docs[0].metadata.get("signature", "")) if docs else None

    # -- global knowledge ----------------------------------------------------------------------
    def index_knowledge_dir(self, knowledge_dir: Path) -> int:
        self.store.delete({"source": "knowledge"})
        docs: list[Doc] = []
        if not knowledge_dir.is_dir():
            return 0
        for path in sorted(knowledge_dir.rglob("*.md")):
            rel = path.relative_to(knowledge_dir)
            top = rel.parts[0] if len(rel.parts) > 1 else "standards"
            doc_type = KNOWLEDGE_TYPES.get(top, "standard")
            meta, body = _front_matter(path.read_text(encoding="utf-8"))
            repo = meta.get("repository", GLOBAL)
            for i, (title, text) in enumerate(chunk_markdown(body)):
                docs.append(
                    Doc(
                        _id("kb", rel.as_posix(), str(i)),
                        f"{meta.get('title', path.stem)} — {title}\n{text}",
                        {
                            "type": doc_type,
                            "repository": repo,
                            "source": "knowledge",
                            "path": rel.as_posix(),
                            "title": f"{meta.get('title', path.stem)} / {title}",
                        },
                    )
                )
        return self._put(docs)

    def index_cost_reports(self, reports: CostReports) -> int:
        self.store.delete({"type": "cost_report"})
        docs: list[Doc] = []
        for rep in reports.reports:
            total_gb = rep.total_gb_day
            ranked = sorted(rep.services.items(), key=lambda kv: -float(kv[1].get("usd_month", 0)))
            lines = [
                f"Platform IBM Cloud Logs cost report {rep.period}: ${rep.total_usd_month:,.0f}/month, {total_gb:,.0f} GB/day."
            ]
            for name, svc in ranked:
                share = float(svc.get("usd_month", 0)) / rep.total_usd_month * 100 if rep.total_usd_month else 0
                lines.append(
                    f"- {name}: ${float(svc.get('usd_month', 0)):,.0f}/month ({share:.1f}%), {float(svc.get('gb_per_day', 0)):,.1f} GB/day"
                )
            if rep.notes:
                lines.append(rep.notes)
            docs.append(
                Doc(
                    _id("cost", rep.period, "platform"),
                    "\n".join(lines),
                    {
                        "type": "cost_report",
                        "repository": GLOBAL,
                        "source": "cost_report",
                        "path": rep.path.name,
                        "title": f"Platform cost report {rep.period}",
                    },
                )
            )
            for name, svc in rep.services.items():
                share = float(svc.get("usd_month", 0)) / rep.total_usd_month * 100 if rep.total_usd_month else 0
                sev = ", ".join(f"{k} {v} GB/day" for k, v in (svc.get("by_severity_gb_day") or {}).items())
                text = (
                    f"Cost report {rep.period} for {name}: ${float(svc.get('usd_month', 0)):,.0f}/month "
                    f"({share:.1f}% of platform), {float(svc.get('gb_per_day', 0)):,.1f} GB/day. Volume by severity: {sev}. "
                    f"{svc.get('notes', '')}"
                )
                docs.append(
                    Doc(
                        _id("cost", rep.period, name),
                        text,
                        {
                            "type": "cost_report",
                            "repository": name,
                            "source": "cost_report",
                            "path": rep.path.name,
                            "title": f"{name} cost {rep.period}",
                        },
                    )
                )
        return self._put(docs)

    # -- repository knowledge ----------------------------------------------------------------------
    def index_repository(
        self,
        analysis: RepoAnalysis,
        docs_found: list[tuple[str, str]],
        commits: list[tuple[CommitInfo, list[str], float]],
        signature: str,
    ) -> dict[str, int]:
        repo = analysis.repository
        self.store.delete({"$and": [{"repository": repo}, {"source": "repository"}]})
        base = {"repository": repo, "source": "repository"}
        counts = {"log_statement": 0, "finding": 0, "pattern": 0, "readme": 0, "pr_history": 0}

        stmts: list[Doc] = []
        for s in analysis.statements:
            flags = [
                n
                for n, on in (
                    ("loop", s.in_loop),
                    ("retry", s.in_retry),
                    ("polling", s.in_polling),
                    ("consumer", s.in_consumer),
                    ("error-path", s.in_error_path),
                    ("entry-exit", s.entry_exit),
                )
                if on
            ]
            payload = ", ".join(f"{p.expr} ({p.kind.value.lower()})" for p in s.payload_args)
            text = (
                f"{repo} {s.file}:{s.line} {s.language} {s.level.value} log in {s.function or 'package scope'}. "
                f"Context: {', '.join(flags) or 'plain'}. Message: {s.message}. Arguments: {', '.join(s.args)}. "
                + (f"Whole-object payload logged: {payload}. " if payload else "")
                + f"Code: {s.snippet}"
            )
            stmts.append(
                Doc(
                    _id("stmt", repo, s.id),
                    text,
                    {
                        **base,
                        "type": "log_statement",
                        "path": s.file,
                        "line": s.line,
                        "level": s.level.value,
                        "title": f"{s.file}:{s.line}",
                    },
                )
            )
        counts["log_statement"] = self._put(stmts)

        fdocs = [
            Doc(
                _id("finding", repo, f.id),
                f"{repo} finding: {f.title} at {f.file}:{f.line} in {f.function or 'package scope'} ({f.level.value}, "
                f"impact {f.impact.value}, confidence {f.confidence.value}, rule {f.rule}). {f.reason} "
                f"Recommendation: {f.recommendation} Estimated savings {f.est_savings_pct:.1f}% of repository cost. "
                f"Code: {f.snippet}",
                {
                    **base,
                    "type": "finding",
                    "path": f.file,
                    "line": f.line,
                    "level": f.level.value,
                    "impact": f.impact.value,
                    "title": f"{f.title} ({f.file}:{f.line})",
                },
            )
            for f in analysis.findings
        ]
        counts["finding"] = self._put(fdocs)

        m = analysis.metrics
        reasons = "; ".join(
            f"{r.reason} ({r.impact.value}, {r.count} sites, ~{r.share_pct}% of cost)" for r in analysis.top_reasons[:6]
        )
        pattern = (
            f"Logging pattern summary for {repo} ({', '.join(analysis.languages)}): {m['statements']} logging statements in "
            f"{analysis.files_scanned} files. By level: {m['by_level']}. Risk {analysis.risk_level.value}; optimisation "
            f"potential {analysis.potential_low_pct}-{analysis.potential_high_pct}%. Estimated {analysis.cost.gb_per_day} GB/day, "
            f"${analysis.cost.usd_per_month:,.0f}/month ({analysis.cost.basis}). Top reasons: {reasons or 'none'}. "
            f"Payload-logging statements: {m['payload_statements']}; duplicate: {m['duplicate_statements']}; "
            f"high-frequency: {m['high_frequency_statements']}; misclassified ERROR: {m['misclassified_error_statements']}."
        )
        counts["pattern"] = self._put(
            [
                Doc(
                    _id("pattern", repo),
                    pattern,
                    {**base, "type": "pattern", "signature": signature, "title": f"{repo} logging pattern"},
                )
            ]
        )

        rdocs = []
        for path, text in docs_found:
            for i, (title, chunk) in enumerate(chunk_markdown(text)):
                rdocs.append(
                    Doc(
                        _id("doc", repo, path, str(i)),
                        f"{repo} {path} — {title}\n{chunk}",
                        {**base, "type": "readme", "path": path, "title": f"{path} / {title}"},
                    )
                )
        counts["readme"] = self._put(rdocs)

        pdocs = []
        for c, added, gb in commits:
            kinds = "; ".join(added[:5]) or "no costly logging patterns"
            pdocs.append(
                Doc(
                    _id("pr", repo, c.sha),
                    f"{repo} commit {c.sha} on {c.date}: {c.subject}. Logging impact ≈ {gb:+.2f} GB/day. Introduced: {kinds}.",
                    {
                        **base,
                        "type": "pr_history",
                        "commit": c.sha,
                        "date": c.date,
                        "gb_day": round(gb, 3),
                        "costly": bool(added),
                        "title": f"{c.sha} {c.subject[:60]}",
                    },
                )
            )
        counts["pr_history"] = self._put(pdocs)
        return counts


# ---------------------------------------------------------------------------------------------
class Retriever:
    """Repository-scoped hybrid retrieval: vector similarity plus a small lexical boost, per-type quotas."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def search(self, query: str, repository: str | None, types: list[str] | None = None, k: int = 6) -> list[Hit]:
        emb = self.kb.embedder.embed_query(query)
        clauses: list[dict] = []
        if repository:
            clauses.append({"repository": {"$in": [repository, GLOBAL]}})
        if types:
            clauses.append({"type": {"$in": types}})
        where = {"$and": clauses} if len(clauses) > 1 else clauses[0] if clauses else None
        hits = self.kb.store.query(emb, k * 3, where)
        q_terms = {t for t in re.findall(r"[a-z0-9]{3,}", query.lower())}
        for h in hits:
            terms = set(re.findall(r"[a-z0-9]{3,}", h.text.lower()))
            overlap = len(q_terms & terms) / (len(q_terms) or 1)
            h.score = 0.8 * h.score + 0.2 * overlap
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    def context(self, query: str, repository: str | None, k_per_group: int = 4) -> list[Hit]:
        """Balanced evidence set for an answer: repo facts + standards/IBM guidance + cost + history."""
        groups: list[tuple[list[str], int]] = [
            (["finding", "pattern", "log_statement"], k_per_group + 2),
            (["standard", "ibm_guidance"], k_per_group),
            (["cost_report", "incident", "pr_history", "readme"], k_per_group),
        ]
        seen: set[str] = set()
        out: list[Hit] = []
        for types, k in groups:
            for h in self.search(query, repository, types, k):
                if h.id not in seen:
                    seen.add(h.id)
                    out.append(h)
        return out
