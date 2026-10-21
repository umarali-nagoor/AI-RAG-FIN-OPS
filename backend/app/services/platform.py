"""Platform: shared state, analysis cache and knowledge-base synchronisation."""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.analysis.analyzer import analyze_statements
from app.analysis.costreport import CostReports
from app.analysis.pr import review_diff
from app.core.config import Settings
from app.ingest.repos import (
    RepositoryRegistry,
    collect_docs,
    head_commit,
    iter_source_files,
    recent_commits,
    scan_repository,
)
from app.llm.providers import Embedder, LLMProvider, build_embedder, build_llm
from app.models import Impact, RepoAnalysis, RepositoryInfo
from app.rag.indexer import KnowledgeBase, Retriever
from app.rag.store import ChromaStore

log = logging.getLogger(__name__)


@dataclass
class HistoryEntry:
    sha: str
    date: str
    subject: str
    gb_day: float
    findings: list[str] = field(default_factory=list)
    risk: Impact = Impact.LOW


@dataclass
class RepoState:
    analysis: RepoAnalysis
    history: list[HistoryEntry]
    signature: str
    root: Path


class Platform:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMProvider | None = None,
        embedder: Embedder | None = None,
        in_memory: bool = False,
    ):
        self.settings = settings
        self.registry = RepositoryRegistry(settings)
        self.reports = CostReports(settings.cost_reports_dir)
        self.llm = llm or build_llm(settings)
        self.embedder = embedder or build_embedder(settings)
        path = None if in_memory else str((settings.data_dir / "chroma").resolve())
        if path:
            Path(path).mkdir(parents=True, exist_ok=True)
        self.store = ChromaStore(path, f"finops-{self.embedder.signature}")
        self.kb = KnowledgeBase(self.store, self.embedder)
        self.retriever = Retriever(self.kb)
        self._states: dict[str, RepoState] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.ready = False

    # -- lifecycle -------------------------------------------------------------------------------
    def bootstrap(self) -> None:
        """Index global knowledge (standards, IBM guidance, incidents, cost reports)."""
        k = self.kb.index_knowledge_dir(self.settings.knowledge_dir)
        c = self.kb.index_cost_reports(self.reports)
        log.info("knowledge base ready", extra={"knowledge_chunks": k, "cost_report_docs": c})
        self.ready = True

    def reload_reports(self) -> None:
        self.reports.reload()
        self.kb.index_cost_reports(self.reports)
        with self._guard:
            self._states.clear()

    # -- repository state -----------------------------------------------------------------------
    def _lock(self, name: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(name, threading.Lock())

    def _signature(self, name: str, root: Path) -> str:
        s = self.settings
        h = hashlib.sha1(usedforsecurity=False)
        h.update(f"{head_commit(root)}|{s.priority_retention_days}|{s.debug_enabled_in_prod}|".encode())
        h.update(f"{self.embedder.signature}|{s.history_commits}|".encode())
        for p in sorted(iter_source_files(root, s.max_file_bytes)):
            st = p.stat()
            h.update(f"{p.relative_to(root)}:{st.st_size}:{int(st.st_mtime)}|".encode())
        latest = self.reports.latest
        h.update(f"{latest.period if latest else '-'}:{latest.path.stat().st_mtime if latest else 0}".encode())
        return h.hexdigest()[:16]

    def state(self, name: str, *, index: bool = True, force: bool = False) -> RepoState:
        """Return (and lazily build) the analysis for a configured repository. Raises KeyError / FileNotFoundError."""
        self.registry.get(name)  # KeyError for unknown repositories
        with self._lock(name):
            root = self.registry.resolve(name)
            if root is None:
                raise FileNotFoundError(name)
            sig = self._signature(name, root)
            cached = self._states.get(name)
            if cached and cached.signature == sig and not force:
                return cached
            statements, files, langs = scan_repository(name, root, self.settings.max_file_bytes)
            calibration = self.reports.calibration(name)
            analysis = analyze_statements(
                self.settings,
                name,
                statements,
                calibration=calibration,
                files_scanned=files,
                languages=langs,
                commit=head_commit(root),
            )
            # Fetch commits once and reuse for both history and indexing.
            commits = recent_commits(root, self.settings.history_commits, self.settings.max_diff_bytes)
            history = self._history(name, analysis, commits)
            state = RepoState(analysis, history, sig, root)
            self._states[name] = state
            if index:
                self._index(state, commits)
            return state

    def _history(self, name: str, analysis: RepoAnalysis, commits: list) -> list[HistoryEntry]:
        out: list[HistoryEntry] = []
        calibration = self.reports.calibration(name)
        for c in commits:
            try:
                r = review_diff(self.settings, name, c.diff, analysis.statements, calibration, None)
            except Exception:  # noqa: BLE001 - one malformed historical diff must not break the repository
                log.warning("history diff skipped", extra={"repository": name, "commit": c.sha})
                continue
            out.append(
                HistoryEntry(
                    c.sha, c.date, c.subject, r.net_gb_day, [f"{f.type} at {f.location}" for f in r.findings], r.risk
                )
            )
        return out

    def _index(self, state: RepoState, commits: list) -> None:
        a = state.analysis
        if self.kb.indexed_signature(a.repository) == state.signature:
            return
        docs = collect_docs(state.root)
        commit_entries = [
            (c, h.findings, h.gb_day)
            for c, h in zip(commits, state.history, strict=False)
        ]
        counts = self.kb.index_repository(a, docs, commit_entries, state.signature)
        log.info("repository indexed", extra={"repository": a.repository, **counts})

    def reindex(self, name: str | None = None) -> list[str]:
        names = [name] if name else self.registry.names()
        done = []
        for n in names:
            try:
                self.registry.resolve(n, sync=True)
                self.state(n, force=True)
                done.append(n)
            except (FileNotFoundError, KeyError):
                log.warning("repository unavailable for indexing", extra={"repository": n})
        return done

    # -- listing --------------------------------------------------------------------------------
    def repositories(self) -> list[RepositoryInfo]:
        out = []
        for name, spec in self.registry.specs.items():
            st = self._states.get(name)
            available = bool((spec.path and spec.path.is_dir()) or spec.url)
            out.append(
                RepositoryInfo(
                    name=name,
                    description=spec.description,
                    languages=st.analysis.languages if st else [],
                    available=available,
                    indexed=st is not None,
                    commit=st.analysis.commit if st else None,
                )
            )
        return out

    def all_states(self) -> dict[str, RepoState]:
        out = {}
        for name in self.registry.names():
            try:
                out[name] = self.state(name)
            except FileNotFoundError:
                log.warning("repository not available", extra={"repository": name})
        return out
