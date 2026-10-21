"""Repository registry, loader and git history (GitPython).

Security notes:
  * Only repositories declared in config/repositories.yaml can be analysed (allow-list; no user-supplied paths).
  * Remote URLs must be https. Credentials are passed to git through the process environment
    (http.extraHeader), never embedded in the URL and never logged.
  * File reads are confined to the repository root.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from app.core.config import Settings
from app.ingest.extractor import EXTENSIONS, extract_file
from app.models import LogStatement

log = logging.getLogger(__name__)

SKIP_DIRS = {
    ".git",
    "vendor",
    "node_modules",
    "target",
    "build",
    "dist",
    "third_party",
    "thirdparty",
    "generated",
    "gen",
    "mocks",
    "mock",
    "__pycache__",
    ".venv",
    "venv",
    "testdata",
    "fixtures",
    "e2e",
    "test",
    "tests",
}
TEST_FILE = re.compile(
    r"(_test\.go|Test\.java|Tests\.java|IT\.java|^test_.*\.py|_test\.py|conftest\.py|\.pb\.go|_gen\.go)$"
)
DOC_NAMES = re.compile(r"(readme|architecture|design|logging|runbook|adr|incident|postmortem|changelog)", re.I)
MAX_DOCS = 40


@dataclass
class RepoSpec:
    name: str
    description: str = ""
    path: Path | None = None
    url: str | None = None
    branch: str = "main"
    root: Path | None = field(default=None, init=False)  # resolved checkout path


class RepositoryRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.specs: dict[str, RepoSpec] = {}
        self._load_config()

    def _load_config(self) -> None:
        cfg = self.settings.repos_config
        if not cfg.is_file():
            log.warning("repositories config not found", extra={"path": str(cfg)})
            return
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        base = cfg.parent.resolve()

        # ------------------------------------------------------------------
        # 1. Explicit repository entries (path or full URL)
        # ------------------------------------------------------------------
        for item in raw.get("repositories", []):
            url = item.get("url")
            if url and not str(url).startswith("https://"):
                raise ValueError(f"repository {item.get('name')}: only https:// URLs are allowed")
            path = None
            if item.get("path"):
                path = Path(item["path"])
                path = path if path.is_absolute() else (base / path).resolve()
            spec = RepoSpec(
                name=item["name"],
                description=item.get("description", ""),
                path=path,
                url=url,
                branch=item.get("branch", "main"),
            )
            self.specs[spec.name] = spec

        # ------------------------------------------------------------------
        # 2. GitHub shorthand blocks: org + list of repo names
        #    Each entry builds a https://github.com/{org}/{repo} URL.
        #    Explicit `repositories:` entries above take precedence (not overwritten).
        # ------------------------------------------------------------------
        for block in raw.get("github_repos", []):
            org = block.get("org", "").strip()
            if not org:
                log.warning("github_repos block missing 'org', skipping")
                continue
            for entry in block.get("repos", []):
                if isinstance(entry, str):
                    repo_name = entry.strip()
                    description = ""
                    branch = "main"
                else:
                    repo_name = str(entry.get("name", "")).strip()
                    description = entry.get("description", "")
                    branch = entry.get("branch", "main")
                if not repo_name:
                    continue
                if repo_name in self.specs:
                    # explicit entry takes precedence
                    continue
                url = f"https://github.com/{org}/{repo_name}"
                spec = RepoSpec(
                    name=repo_name,
                    description=description or f"{org}/{repo_name}",
                    url=url,
                    branch=branch,
                )
                self.specs[spec.name] = spec
                log.debug("registered github repo", extra={"org": org, "repo": repo_name, "url": url})

    # ------------------------------------------------------------------------------------------
    def names(self) -> list[str]:
        return list(self.specs)

    def get(self, name: str) -> RepoSpec:
        if name not in self.specs:
            raise KeyError(name)
        return self.specs[name]

    def resolve(self, name: str, sync: bool = False) -> Path | None:
        """Return the local checkout path for a configured repository (cloning/pulling when it has a URL)."""
        spec = self.get(name)
        if spec.path and spec.path.is_dir() and not (spec.url and sync):
            spec.root = spec.path
            return spec.root
        if spec.url:
            target = (self.settings.data_dir / "repos" / spec.name).resolve()
            try:
                self._clone_or_pull(spec, target)
            except GitCommandError:
                log.error("git sync failed", extra={"repository": name})
                return target if target.is_dir() else None
            spec.root = target
            return target
        spec.root = spec.path if spec.path and spec.path.is_dir() else None
        return spec.root

    def _git_env(self) -> dict[str, str]:
        token = self.settings.git_token
        if not token:
            return {}
        return {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token.get_secret_value()}",
            "GIT_TERMINAL_PROMPT": "0",
        }

    def _clone_or_pull(self, spec: RepoSpec, target: Path) -> None:
        env = self._git_env()
        depth = max(self.settings.history_commits, 50)
        if (target / ".git").is_dir():
            repo = Repo(target)
            with repo.git.custom_environment(**env):
                repo.git.fetch("--depth", str(depth), "origin", spec.branch)
                repo.git.reset("--hard", f"origin/{spec.branch}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            Repo.clone_from(spec.url, target, branch=spec.branch, depth=depth, env={**os.environ, **env})


# --------------------------------------------------------------------------------------------------
def head_commit(root: Path) -> str | None:
    try:
        return Repo(root, search_parent_directories=False).head.commit.hexsha[:12]
    except (InvalidGitRepositoryError, NoSuchPathError, ValueError):
        return None


def iter_source_files(root: Path, max_bytes: int) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext not in EXTENSIONS or TEST_FILE.search(fn):
                continue
            p = Path(dirpath) / fn
            try:
                if p.is_symlink() or p.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            yield p


def scan_repository(name: str, root: Path, max_bytes: int) -> tuple[list[LogStatement], int, list[str]]:
    statements: list[LogStatement] = []
    files = 0
    langs: set[str] = set()
    for p in iter_source_files(root, max_bytes):
        rel = p.relative_to(root).as_posix()
        try:
            data = p.read_bytes()
        except OSError:
            continue
        files += 1
        found = extract_file(name, rel, data)
        if found:
            langs.add(found[0].language)
        statements.extend(found)
    return statements, files, sorted(langs)


def read_base_file(root: Path, rel_path: str, max_bytes: int) -> bytes | None:
    """Read a file from the checkout, refusing paths that escape the repository root."""
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        return None
    if candidate.stat().st_size > max_bytes:
        return None
    return candidate.read_bytes()


def collect_docs(root: Path, max_bytes: int = 200_000) -> list[tuple[str, str]]:
    """README / architecture / runbook style documents for the knowledge base."""
    docs: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.lower().endswith((".md", ".rst", ".txt")) or not DOC_NAMES.search(fn):
                continue
            p = Path(dirpath) / fn
            try:
                if p.stat().st_size > max_bytes:
                    continue
                docs.append((p.relative_to(root).as_posix(), p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
            if len(docs) >= MAX_DOCS:
                return docs
    return docs


@dataclass
class CommitInfo:
    sha: str
    date: str
    subject: str
    diff: str


def recent_commits(root: Path, limit: int, max_diff_bytes: int) -> list[CommitInfo]:
    """Recent non-merge commits with their unified diff (source files only). Author identity is not collected."""
    try:
        repo = Repo(root)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return []
    out: list[CommitInfo] = []
    try:
        for c in repo.iter_commits(max_count=limit):
            if len(c.parents) != 1:
                continue
            diff = repo.git.diff(
                c.parents[0].hexsha, c.hexsha, "--unified=3", "--no-color", "--", "*.go", "*.java", "*.py"
            )
            if not diff.strip() or len(diff.encode()) > max_diff_bytes:
                continue
            out.append(CommitInfo(c.hexsha[:12], c.committed_datetime.date().isoformat(), c.summary[:140], diff))
    except (GitCommandError, ValueError):
        log.warning("could not read history", extra={"root": root.name})
    return out
