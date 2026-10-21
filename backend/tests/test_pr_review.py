from app.analysis.pr import review_diff
from app.ingest.diff import apply_hunks, parse_diff

BAD = """diff --git a/handlers/resource_handler.go b/handlers/resource_handler.go
--- a/handlers/resource_handler.go
+++ b/handlers/resource_handler.go
@@ -41,3 +41,10 @@ func (h *ResourceHandler) GetWorkspace(ctx context.Context, id string) (*Workspace, error) {
 	h.logger.Infof("Workspace resource: %+v", ws)
 	h.logger.Infof("Exiting GetWorkspace")
 	return ws, nil
 }
+
+func (h *ResourceHandler) ListWorkspaces(ctx context.Context, request *ListRequest) ([]*Workspace, error) {
+	h.logger.Infof("Entering ListWorkspaces")
+	h.logger.Infof("List request: %+v", request)
+	all, err := h.service.List(ctx, request)
+	if err != nil {
+		h.logger.Errorf("workspace not found: %v", err)
+		return nil, err
+	}
+	for _, w := range all {
+		h.logger.Infof("workspace %+v", w)
+	}
+	h.logger.Infof("Exiting ListWorkspaces")
+	return all, nil
+}
"""

GOOD = """diff --git a/handlers/resource_handler.go b/handlers/resource_handler.go
--- a/handlers/resource_handler.go
+++ b/handlers/resource_handler.go
@@ -22,5 +22,5 @@
-	h.logger.Infof("Request received: %+v", request)
+	h.logger.Infof("Request received requestId=%s", request.ID)
 	if request.Name == "" {
"""

NO_CODE = "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n # x\n+more\n"


def test_parse_diff_tracks_added_and_removed_lines():
    f = parse_diff(GOOD)[0]
    assert f.path == "handlers/resource_handler.go" and f.added_lines == {25} - {25} | {f.hunks[0].new_start + 0}
    assert f.removed_lines == {f.hunks[0].old_start}


def test_apply_hunks_requires_matching_context():
    f = parse_diff("--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n")[0]
    assert apply_hunks("a\nb\n", f.hunks) == "a\nc\n"
    assert apply_hunks("a\nzzz\n", f.hunks) is None


def test_costly_pr_is_high_risk_with_expected_findings(copilot):
    r = copilot.review_pr("schematics-api", BAD)
    kinds = {f.type for f in r.findings}
    assert r.risk.value == "HIGH" and r.decision == "BLOCK"
    assert {"Payload Logging", "Loop Logging"} <= kinds
    assert r.estimatedCostImpact.startswith("+") and r.estimatedUsdPerMonth > 0
    assert r.newLogStatements >= 5
    assert "requestId" in r.recommendation
    payload = next(f for f in r.findings if f.type == "Payload Logging")
    assert payload.suggestedCode and "requestId" in payload.suggestedCode


def test_entry_exit_logs_in_pr_are_not_flagged(copilot):
    r = copilot.review_pr("schematics-api", BAD)
    assert all("Entering" not in f.reason and "Exiting" not in f.reason for f in r.findings)


def test_pr_that_removes_payload_logging_is_safe(copilot):
    r = copilot.review_pr("schematics-api", GOOD)
    assert r.risk.value == "LOW" and r.decision == "PASS" and r.findings == []
    assert r.estimatedCostImpact.startswith("-")


def test_pr_without_source_changes(copilot):
    r = copilot.review_pr("schematics-api", NO_CODE)
    assert r.risk.value == "LOW" and r.findings == [] and "no Go, Java or Python" in r.summary


def test_new_file_diff_without_base(platform):
    diff = (
        "diff --git a/newpkg/x.py b/newpkg/x.py\nnew file mode 100644\n--- /dev/null\n+++ b/newpkg/x.py\n"
        "@@ -0,0 +1,4 @@\n+def handle(request):\n+    for i in range(3):\n+        logger.info('req %s', request)\n+    return 1\n"
    )
    st = platform.state("schematics-agents-api")
    res = review_diff(platform.settings, "schematics-agents-api", diff, st.analysis.statements, None, st.root)
    assert res.findings and res.net_gb_day > 0


def test_new_critical_log_in_hot_path_is_flagged(platform):
    diff = (
        "diff --git a/svc/x.py b/svc/x.py\nnew file mode 100644\n--- /dev/null\n+++ b/svc/x.py\n"
        "@@ -0,0 +1,3 @@\n+def consume_events(events):\n+    for e in events:\n+        logger.critical('bad event %s', e.id)\n"
    )
    st = platform.state("schematics-agents-api")
    res = review_diff(platform.settings, "schematics-agents-api", diff, st.analysis.statements, None, st.root)
    assert any(f.level.value == "CRITICAL" for f in res.findings)


def test_history_detects_the_costly_commit(platform):
    hist = platform.state("schematics-api").history
    costly = [h for h in hist if h.findings and h.gb_day > 0]
    assert costly and "watcher" in costly[0].subject.lower()
