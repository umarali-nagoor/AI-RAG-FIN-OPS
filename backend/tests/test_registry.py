"""Tests for RepositoryRegistry — focusing on the github_repos shorthand expansion."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

os.environ.setdefault("FINOPS_NO_AUTOAPP", "1")


@pytest.fixture(scope="session", autouse=True)
def _require_sample_repos():  # noqa: PT004  — override the conftest version for this module
    """Override: these tests do not need sample repos on disk."""


def _make_registry(tmp_path: Path, yaml_text: str):
    from app.core.config import Settings
    from app.ingest.repos import RepositoryRegistry

    cfg = tmp_path / "repositories.yaml"
    cfg.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        repos_config=cfg,
    )
    return RepositoryRegistry(settings)


# ---------------------------------------------------------------------------
# github_repos shorthand
# ---------------------------------------------------------------------------


def test_github_repos_plain_strings(tmp_path):
    registry = _make_registry(
        tmp_path,
        """
        github_repos:
          - org: my-org
            repos:
              - service-a
              - service-b
        """,
    )
    assert set(registry.names()) == {"service-a", "service-b"}
    assert registry.get("service-a").url == "https://github.com/my-org/service-a"
    assert registry.get("service-b").url == "https://github.com/my-org/service-b"
    assert registry.get("service-a").branch == "main"


def test_github_repos_object_entry(tmp_path):
    registry = _make_registry(
        tmp_path,
        """
        github_repos:
          - org: my-org
            repos:
              - name: service-c
                description: "The C service"
                branch: develop
        """,
    )
    spec = registry.get("service-c")
    assert spec.url == "https://github.com/my-org/service-c"
    assert spec.description == "The C service"
    assert spec.branch == "develop"


def test_explicit_repo_takes_precedence_over_github_repos(tmp_path):
    """An entry in `repositories:` must not be overwritten by github_repos."""
    registry = _make_registry(
        tmp_path,
        """
        repositories:
          - name: service-a
            description: "local override"
            url: https://github.com/other-org/service-a

        github_repos:
          - org: my-org
            repos:
              - service-a
        """,
    )
    # only one entry, and it keeps the explicit URL
    assert registry.get("service-a").url == "https://github.com/other-org/service-a"
    assert registry.get("service-a").description == "local override"


def test_github_repos_missing_org_skipped(tmp_path):
    registry = _make_registry(
        tmp_path,
        """
        github_repos:
          - repos:
              - service-x
        """,
    )
    assert registry.names() == []
