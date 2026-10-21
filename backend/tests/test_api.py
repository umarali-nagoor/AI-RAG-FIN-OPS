import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app

DIFF = (
    "diff --git a/handlers/resource_handler.go b/handlers/resource_handler.go\n--- a/handlers/resource_handler.go\n"
    "+++ b/handlers/resource_handler.go\n@@ -22,5 +22,5 @@\n"
    '-\th.logger.Infof("Request received: %+v", request)\n+\th.logger.Infof("Request %+v", request)\n'
    ' \tif request.Name == "" {\n'
)


def test_analyze_repository_contract(client):
    r = client.post("/analyze-repository", json={"repository": "icl-test-schematics-api"})
    assert r.status_code == 200
    b = r.json()
    assert b["repository"] == "icl-test-schematics-api" and b["riskLevel"] == "HIGH"
    assert {"reason", "impact"} == set(b["topReasons"][0])
    assert b["estimatedOptimizationPotential"].endswith("%")
    assert "workflow/reconciler.go" in b["filesIdentified"]
    # platformCostShare may be None when cost reports use old repo names (no calibration match)
    assert b["platformCostShare"] is None or b["platformCostShare"].endswith("%")
    assert {s["type"] for s in b["sources"]} >= {"finding"}


def test_optimize_repository_contract(client):
    r = client.post("/optimize-repository", json={"repository": "icl-test-schematics-api"})
    b = r.json()
    assert r.status_code == 200 and b["recommendations"]
    rec = b["recommendations"][0]
    assert {"issue", "location", "recommendation", "estimatedSavings"} <= set(rec)
    assert any("entry/exit" in g for g in b["guardrails"])


def test_review_pr_contract(client):
    r = client.post("/review-pr", json={"repository": "icl-test-schematics-api", "diff": DIFF})
    b = r.json()
    assert r.status_code == 200
    assert {"repository", "risk", "estimatedCostImpact", "findings", "recommendation"} <= set(b)


def test_api_prefix_mirrors_root_routes(client):
    assert client.post("/api/analyze-repository", json={"repository": "icl-test-blueprint-api"}).status_code == 200
    assert client.get("/api/repositories").json()[0]["name"]


def test_unknown_repository_is_404_and_generic(client):
    r = client.post("/analyze-repository", json={"repository": "nope"})
    assert r.status_code == 404 and r.json() == {"error": "Repository not found"}


@pytest.mark.parametrize("bad", ["../etc", "a/b", "x;rm -rf", "", "a" * 101])
def test_repository_name_validation_blocks_traversal(client, bad):
    assert client.post("/analyze-repository", json={"repository": bad}).status_code == 422


def test_extra_fields_rejected_and_validation_error_is_generic(client):
    r = client.post("/analyze-repository", json={"repository": "icl-test-schematics-api", "path": "/etc"})
    assert r.status_code == 422 and r.json() == {"error": "Invalid request"}


def test_ask_routes_by_intent(client):
    root = client.post("/api/ask", json={"question": "Why is icl-test-schematics-api costing more?"}).json()
    assert root["intent"] == "root_cause" and root["repository"] == "icl-test-schematics-api"
    opt = client.post("/api/ask", json={"question": "How can I reduce icl-test-schematics-agents-api costs?"}).json()
    assert opt["intent"] == "optimize" and opt["repository"] == "icl-test-schematics-agents-api"
    plat = client.post("/api/ask", json={"question": "Which repositories have highest estimated logging cost?"}).json()
    assert plat["intent"] == "platform"
    # top-ranked repo varies by estimated cost — check it is one of the configured repos
    assert plat["data"]["ranking"][0]["repository"] in {r["name"] for r in client.get("/api/repositories").json()}
    pr = client.post(
        "/api/ask", json={"question": "Will this PR increase cost?", "repository": "icl-test-schematics-api", "diff": DIFF}
    ).json()
    assert pr["intent"] == "pr_review"


def test_platform_questions_cover_all_repositories(client):
    for q, col in [
        ("Which repositories contain most duplicate logs?", "duplicateStatements"),
        ("Which repositories log largest payloads?", "payloadGbDay"),
        ("Which repositories contain excessive ERROR logs?", "errorRatioPct"),
        ("Which repositories have highest optimization opportunity?", "optimizationPotentialUsdMonth"),
        ("Which repositories recently introduced costly logging patterns?", "recentCostlyCommits"),
    ]:
        d = client.post("/api/ask", json={"question": q}).json()
        assert d["data"]["metric"] == col, q
        assert len(d["data"]["rows"]) >= 10  # grows as repos are added to repositories.yaml
    d = client.post(
        "/api/ask", json={"question": "Which repositories recently introduced costly logging patterns?"}
    ).json()
    # schematics-api has the costly watcher commit; schematics-job-queue-manager also has history
    assert d["data"]["ranking"][0]["repository"].startswith("icl-test-")


def test_guidance_question_uses_knowledge_base(client):
    d = client.post("/api/ask", json={"question": "What does Priority Insights cost with 30 day retention?"}).json()
    assert d["intent"] == "guidance" and "2.25" in d["answer"]


def test_security_headers_and_request_id(client):
    r = client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src" in r.headers["Content-Security-Policy"]


def test_oversized_body_rejected(client):
    r = client.post(
        "/review-pr",
        content=b"x" * 2_000_000,
        headers={"content-length": "2000000", "content-type": "application/json"},
    )
    assert r.status_code == 413


def test_internal_errors_do_not_leak_details(settings, platform, monkeypatch):
    app = create_app(settings, platform)
    with TestClient(app, raise_server_exceptions=False) as c:
        monkeypatch.setattr(
            app.state.copilot, "analyze", lambda *_: (_ for _ in ()).throw(RuntimeError("secret path /x"))
        )
        r = c.post("/analyze-repository", json={"repository": "icl-test-schematics-api"})
    assert r.status_code == 500 and r.json() == {"error": "Internal error"}


def test_auth_required_when_keys_configured(settings, platform):
    s = settings.model_copy(update={"api_keys": SecretStr("k" * 32)})
    with TestClient(create_app(s, platform)) as c:
        assert c.post("/analyze-repository", json={"repository": "icl-test-blueprint-api"}).status_code == 401
        assert (
            c.post(
                "/analyze-repository", json={"repository": "icl-test-blueprint-api"}, headers={"X-API-Key": "wrong"}
            ).status_code
            == 401
        )
        ok = c.post("/analyze-repository", json={"repository": "icl-test-blueprint-api"}, headers={"X-API-Key": "k" * 32})
        assert ok.status_code == 200
        assert c.get("/healthz").status_code == 200  # probes stay unauthenticated


def test_production_refuses_to_start_without_strong_keys(settings, platform):
    with pytest.raises(RuntimeError):
        create_app(Settings(_env_file=None, environment="production", repos_config=settings.repos_config), platform)
    with pytest.raises(RuntimeError):
        create_app(Settings(_env_file=None, environment="production", api_keys=SecretStr("short")), platform)


def test_rate_limit(settings, platform):
    s = settings.model_copy(update={"rate_limit_per_minute": 3})
    with TestClient(create_app(s, platform)) as c:
        codes = [c.get("/api/repositories").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


def test_docs_disabled_in_production(settings, platform):
    s = settings.model_copy(update={"environment": "production", "api_keys": SecretStr("k" * 32)})
    with TestClient(create_app(s, platform)) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
