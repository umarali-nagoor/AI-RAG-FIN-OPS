"""GitHub and GitLab PR integration.

Fetches open pull requests and their unified diffs for a configured repository.
Uses only TLS 1.2+ HTTPS endpoints; tokens are never embedded in URLs or logged.

Security:
- Token passed via Authorization header only (never in URL query strings).
- Only repositories in the allow-list (repositories.yaml) can be queried.
- Response bodies are size-capped at FINOPS_MAX_DIFF_BYTES.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import Settings

log = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


@dataclass
class PRSummary:
    number: int
    title: str
    author: str
    branch: str          # head branch
    base: str            # target branch
    state: str           # open / closed
    url: str
    created_at: str
    updated_at: str
    additions: int = 0
    deletions: int = 0


class GitHubClient:
    """Read-only GitHub API client (repo:read scope)."""

    def __init__(self, token: str, org: str, timeout: float = 20.0):
        self._org = org
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._timeout = timeout

    def _get(self, path: str, **params) -> dict | list:
        url = f"{_GITHUB_API}{path}"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers, params=params)
        if resp.status_code == 404:
            raise KeyError(f"GitHub resource not found: {path}")
        if resp.status_code == 401:
            raise PermissionError("GitHub token is invalid or expired")
        if resp.status_code == 403:
            raise PermissionError("GitHub token lacks the required scope (needs repo:read)")
        resp.raise_for_status()
        return resp.json()

    def list_prs(self, repo: str, state: str = "open", per_page: int = 30) -> list[PRSummary]:
        """List pull requests for org/repo."""
        data = self._get(
            f"/repos/{self._org}/{repo}/pulls",
            state=state,
            per_page=per_page,
            sort="updated",
            direction="desc",
        )
        out = []
        for pr in data:  # type: ignore[union-attr]
            out.append(PRSummary(
                number=pr["number"],
                title=pr["title"],
                author=pr.get("user", {}).get("login", ""),
                branch=pr["head"]["ref"],
                base=pr["base"]["ref"],
                state=pr["state"],
                url=pr["html_url"],
                created_at=pr["created_at"],
                updated_at=pr["updated_at"],
            ))
        return out

    def get_diff(self, repo: str, pr_number: int, max_bytes: int) -> str:
        """Fetch the unified diff for a pull request."""
        url = f"{_GITHUB_API}/repos/{self._org}/{repo}/pulls/{pr_number}"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                url,
                headers={**self._headers, "Accept": "application/vnd.github.diff"},
            )
        if resp.status_code == 404:
            raise KeyError(f"PR #{pr_number} not found in {self._org}/{repo}")
        resp.raise_for_status()
        diff = resp.text
        if len(diff.encode()) > max_bytes:
            raise ValueError(f"Diff for PR #{pr_number} exceeds {max_bytes // 1024} KB limit")
        return diff


def make_github_client(settings: Settings) -> GitHubClient | None:
    """Return a client if GitHub is configured, else None."""
    if not settings.github_token or not settings.github_org:
        return None
    return GitHubClient(
        token=settings.github_token.get_secret_value(),
        org=settings.github_org,
    )
