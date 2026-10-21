from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("FINOPS_NO_AUTOAPP", "1")

BACKEND = Path(__file__).resolve().parents[1]
SAMPLE = BACKEND.parent / "sample-data"
DATA_REPOS = BACKEND / "data" / "repos"


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    from app.core.config import Settings

    return Settings(
        _env_file=None,
        data_dir=tmp_path_factory.mktemp("data"),
        repos_config=BACKEND / "config" / "repositories-test.yaml",
        cost_reports_dir=SAMPLE / "cost-reports",
        knowledge_dir=BACKEND / "knowledge",
        static_dir=BACKEND / "no-static",
        rate_limit_per_minute=0,
    )


@pytest.fixture(scope="session")
def platform(settings):
    from app.services.platform import Platform

    p = Platform(settings, in_memory=True)
    p.bootstrap()
    return p


@pytest.fixture(scope="session")
def copilot(platform):
    from app.services.copilot import Copilot

    return Copilot(platform)


@pytest.fixture(scope="session")
def client(settings, platform):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings, platform)) as c:
        yield c


@pytest.fixture
def tmp_repo(tmp_path):
    def make(files: dict[str, str]) -> Path:
        for rel, text in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        return tmp_path

    return make


@pytest.fixture(scope="session", autouse=True)
def _require_sample_repos():
    if not (DATA_REPOS / "icl-test-schematics-api" / ".git").is_dir():
        pytest.skip("icl-test-* repos not cloned — trigger /admin/reindex first")
    shutil.which("git") or pytest.skip("git not installed")
    # Clone AI-RAG-PR-SAMPLE if not already present (public repo, no token needed)
    target = DATA_REPOS / "AI-RAG-PR-SAMPLE"
    if not (target / ".git").is_dir():
        import subprocess
        subprocess.run(
            ["git", "clone", "--depth", "50",
             "https://github.com/umarali-nagoor/AI-RAG-PR-SAMPLE", str(target)],
            check=False, capture_output=True,
        )
