#!/usr/bin/env bash
# create_sample_prs.sh
# Creates TWO open pull requests per icl-test-* repository (20 PRs total).
# Each PR introduces a different logging anti-pattern so the PR Review feature
# has diverse, realistic diffs to detect and explain.
#
# Anti-pattern coverage across the 20 PRs:
#   Payload Logging      — log.Infof("%+v", obj)  (logs full struct)
#   Loop Logging         — log inside for{} with no rate limiting
#   Polling Log          — log inside infinite for{time.Sleep}
#   Duplicate Logging    — same error logged at service + handler layer
#   Misclassified ERROR  — validation/not-found logged as ERROR not WARNING
#   JSON Blob            — json.Marshal(obj) logged as string
#
# Usage:  bash scripts/create_sample_prs.sh
# Run from: repo root directory
# Prerequisite: gh CLI authenticated, local clones exist in backend/data/repos/
set -euo pipefail

ORG="umarali-nagoor"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_REPOS="$(cd "$SCRIPT_DIR/../backend/data/repos" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}  ! $*${NC}"; }

# ── helper ────────────────────────────────────────────────────────────────────
make_pr() {
  local repo_dir="$1" gh_name="$2" branch="$3" file="$4"
  local title="$5" body="$6" patch="$7"

  existing=$(gh api "repos/${ORG}/${gh_name}/pulls?head=${ORG}:${branch}&state=open" \
    --jq 'length' 2>/dev/null || echo "0")
  if [ "$existing" -gt 0 ]; then
    warn "${gh_name} — PR '${branch}' already exists, skipping"
    return
  fi

  cd "${repo_dir}"
  git fetch origin main -q
  git checkout main -q
  git reset --hard origin/main -q
  git checkout -b "${branch}" -q

  printf '%s' "${patch}" >> "${file}"

  git config user.email "finops-copilot@ibm.com"
  git config user.name "FinOps Copilot"
  git add "${file}"
  git commit -q -m "${title}"
  git push origin "${branch}" --force -q 2>&1 | grep -v "^remote:" || true

  gh pr create \
    --repo "${ORG}/${gh_name}" \
    --head "${branch}" \
    --base main \
    --title "${title}" \
    --body "${body}"

  git checkout main -q
  ok "${gh_name} — PR created: ${title}"
}

echo ""
echo "═══ Creating 2 PRs per repo (20 total) ════════════════════════════════════"


# ════════════════════════════════════════════════════════════════════════════════
# 1. icl-test-schematics-api  (Go)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-api ──"

# PR 1 — Payload Logging: full workspace struct logged on every list call
make_pr \
  "${DATA_REPOS}/icl-test-schematics-api" \
  "icl-test-schematics-api" \
  "feat/list-workspaces" \
  "handlers/resource_handler.go" \
  "feat: add ListWorkspaces with full payload logging" \
  "Adds ListWorkspaces endpoint. Logs full request and each workspace object for debugging." \
'
func (h *ResourceHandler) ListWorkspaces(ctx context.Context, req *ListRequest) ([]*Workspace, error) {
	h.logger.Infof("Entering ListWorkspaces, request: %+v", req)
	all, err := h.service.List(ctx, req)
	if err != nil {
		h.logger.Errorf("list workspaces failed: %v", err)
		return nil, err
	}
	for _, ws := range all {
		h.logger.Infof("workspace: %+v", ws)
	}
	h.logger.Infof("Exiting ListWorkspaces, result: %+v", all)
	return all, nil
}
'

# PR 2 — JSON Blob + Misclassified ERROR: marshals plan to JSON and logs as string; logs 404 as ERROR
make_pr \
  "${DATA_REPOS}/icl-test-schematics-api" \
  "icl-test-schematics-api" \
  "feat/plan-audit-log" \
  "handlers/resource_handler.go" \
  "feat: add plan audit logging and improve error visibility" \
  "Logs the full plan payload as JSON for audit. Improves error reporting for missing workspaces." \
'
func (h *ResourceHandler) AuditPlan(ctx context.Context, id string, plan map[string]interface{}) error {
	b, _ := json.Marshal(plan)
	h.logger.Infof("audit plan for workspace %s: %s", id, string(b))
	ws, err := h.service.Get(ctx, id)
	if ws == nil {
		h.logger.Errorf("workspace %s not found for plan audit", id)
		return ErrNotFound
	}
	return err
}
'


# ════════════════════════════════════════════════════════════════════════════════
# 2. icl-test-blueprint-api  (Go)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-blueprint-api ──"

# PR 1 — Loop Logging: logs every item in a bulk-publish loop
make_pr \
  "${DATA_REPOS}/icl-test-blueprint-api" \
  "icl-test-blueprint-api" \
  "feat/bulk-publish-logging" \
  "pkg/blueprint.go" \
  "feat: add BulkPublish with per-item progress logging" \
  "Adds BulkPublish method. Logs each blueprint item before and after save." \
'
func (s *Service) BulkPublish(ctx context.Context, items []*Blueprint) error {
	for _, item := range items {
		s.log.Infof("publishing blueprint: %+v", item)
		if err := s.store.Save(ctx, item); err != nil {
			s.log.Errorf("save failed: %v", err)
			return err
		}
		s.log.Infof("blueprint published: %+v", item)
	}
	return nil
}
'

# PR 2 — Polling Loop: continuous version-check loop with sleep
make_pr \
  "${DATA_REPOS}/icl-test-blueprint-api" \
  "icl-test-blueprint-api" \
  "feat/version-sync-watcher" \
  "pkg/blueprint.go" \
  "feat: add version sync watcher with polling log" \
  "Adds a background watcher that polls remote catalog for blueprint version changes." \
'
func (s *Service) WatchVersions(ctx context.Context, ids []string) {
	for {
		for _, id := range ids {
			s.log.Infof("checking version for blueprint %s", id)
			v, err := s.remote.LatestVersion(ctx, id)
			if err != nil {
				s.log.Errorf("version check failed for blueprint %s: %v", id, err)
			} else {
				s.log.Infof("blueprint %s latest version: %+v", id, v)
			}
		}
		time.Sleep(5 * time.Second)
	}
}
'


# ════════════════════════════════════════════════════════════════════════════════
# 3. icl-test-schematics-agents-api  (Python)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-agents-api ──"

# PR 1 — Payload Logging: logs full agent dict on registration
make_pr \
  "${DATA_REPOS}/icl-test-schematics-agents-api" \
  "icl-test-schematics-agents-api" \
  "feat/agent-registration-audit" \
  "app/service.py" \
  "feat: log full agent payload on registration for audit" \
  "Adds audit logging on agent registration. Logs complete agent object for traceability." \
'

def audit_register(agent: dict) -> None:
    """Log the full agent payload for registration audit."""
    logger.info("agent registration audit: %s", agent)
    logger.info("agent capabilities: %s", agent.get("capabilities", {}))
    logger.info("agent metadata: %s", agent.get("metadata", {}))
'

# PR 2 — Polling + Duplicate Logging: heartbeat loop logs full object; error logged twice
make_pr \
  "${DATA_REPOS}/icl-test-schematics-agents-api" \
  "icl-test-schematics-agents-api" \
  "feat/agent-health-monitor" \
  "app/service.py" \
  "feat: add agent health monitor with heartbeat polling" \
  "Polls agent health every 5s. Logs full agent object each cycle for observability." \
'

def monitor_health(agents: list) -> None:
    """Continuously monitor agent health."""
    import time
    while True:
        for agent in agents:
            try:
                status = fetch_work(agent["id"])
                logger.info("agent health: %s status: %s", agent, status)
            except Exception as exc:
                logger.error("health check failed for agent %s: %s", agent, exc)
                logger.error("agent dump: %s", agent)
        time.sleep(5)
'


# ════════════════════════════════════════════════════════════════════════════════
# 4. icl-test-schematics-ansible  (Python)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-ansible ──"

# PR 1 — Loop Logging: logs full task result struct per task
make_pr \
  "${DATA_REPOS}/icl-test-schematics-ansible" \
  "icl-test-schematics-ansible" \
  "feat/task-result-audit" \
  "runner/run.py" \
  "feat: log full task result for each Ansible task execution" \
  "Improves audit trail by logging the complete task result object after every task." \
'

def log_task_results(results: list) -> None:
    """Log every task result for audit."""
    for result in results:
        logger.info("task result: %s", result)
        logger.info("task host facts: %s", getattr(result, "_host", {}))
'

# PR 2 — Retry Polling: polls playbook status with full object log in loop
make_pr \
  "${DATA_REPOS}/icl-test-schematics-ansible" \
  "icl-test-schematics-ansible" \
  "feat/playbook-status-poller" \
  "runner/run.py" \
  "feat: add playbook status poller with retry logging" \
  "Adds a status polling loop that waits for playbook completion. Logs full status object each poll." \
'

def poll_playbook_status(runner, timeout: int = 300) -> str:
    """Poll until playbook completes or timeout."""
    import time
    elapsed = 0
    while elapsed < timeout:
        status = runner.get_status()
        logger.info("playbook status poll: %s", status)
        if status.get("rc") is not None:
            logger.info("playbook completed: %s", status)
            return status["rc"]
        time.sleep(5)
        elapsed += 5
    logger.error("playbook timed out after %ds, last status: %s", timeout, status)
    return "timeout"
'


# ════════════════════════════════════════════════════════════════════════════════
# 5. icl-test-schematics-data-job  (Python)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-data-job ──"

# PR 1 — Loop Logging: logs every exported row
make_pr \
  "${DATA_REPOS}/icl-test-schematics-data-job" \
  "icl-test-schematics-data-job" \
  "feat/row-level-audit-log" \
  "jobs/export.py" \
  "feat: add per-row audit logging for data export compliance" \
  "Adds row-level audit logging to satisfy compliance requirements. Logs each record before write." \
'

def export_with_audit(rows: list, sink) -> int:
    """Export rows with per-row audit trail."""
    written = 0
    for row in rows:
        logger.info("exporting row: %s", row)
        sink.write(row)
        logger.info("row written: %s", row)
        written += 1
    return written
'

# PR 2 — Misclassified ERROR: logs missing-partition as ERROR instead of WARNING
make_pr \
  "${DATA_REPOS}/icl-test-schematics-data-job" \
  "icl-test-schematics-data-job" \
  "feat/partition-validator" \
  "jobs/export.py" \
  "feat: add partition validation with error reporting" \
  "Validates that all required partitions exist before export. Reports missing partitions." \
'

def validate_partitions(partitions: list, required: list) -> bool:
    """Validate required partitions are present."""
    for p in required:
        if p not in partitions:
            logger.error("partition %s not found, skipping export", p)
            return False
    return True
'


# ════════════════════════════════════════════════════════════════════════════════
# 6. icl-test-schematics-job  (Go)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-job ──"

# PR 1 — Payload Logging + Loop: logs full step struct inside execution loop
make_pr \
  "${DATA_REPOS}/icl-test-schematics-job" \
  "icl-test-schematics-job" \
  "feat/step-execution-logging" \
  "cmd/main.go" \
  "feat: add step-level execution logging for Terraform job runner" \
  "Logs the full step configuration before and after execution for better debuggability." \
'
func runSteps(steps []*Step, log *Logger) error {
	for _, step := range steps {
		log.Infof("executing step: %+v", step)
		if err := step.Run(); err != nil {
			log.Errorf("step failed, config: %+v error: %v", step, err)
			return err
		}
		log.Infof("step completed: %+v", step)
	}
	return nil
}
'

# PR 2 — Polling: watches job status in infinite loop with full object log
make_pr \
  "${DATA_REPOS}/icl-test-schematics-job" \
  "icl-test-schematics-job" \
  "feat/job-status-watcher" \
  "cmd/main.go" \
  "feat: add job status watcher with continuous polling" \
  "Watches job completion status in a polling loop. Logs full job state each cycle." \
'
func watchJobStatus(jobID string, client JobClient, log *Logger) {
	for {
		job, err := client.Get(jobID)
		if err != nil {
			log.Errorf("status poll failed for job %s: %v", jobID, err)
		} else {
			log.Infof("job status: %+v", job)
		}
		time.Sleep(2 * time.Second)
	}
}
'


# ════════════════════════════════════════════════════════════════════════════════
# 7. icl-test-schematics-job-queue-manager  (Java)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-job-queue-manager ──"

# PR 1 — Loop Logging: logs full job object during queue drain
make_pr \
  "${DATA_REPOS}/icl-test-schematics-job-queue-manager" \
  "icl-test-schematics-job-queue-manager" \
  "feat/queue-drain-logging" \
  "src/main/java/com/ibm/schematics/queue/JobConsumer.java" \
  "feat: log full job object during scheduled queue drain" \
  "Adds verbose logging during scheduled queue drain for operational visibility." \
'

    public void drainQueue(List<Job> pending) {
        for (Job job : pending) {
            log.info("draining job from queue: {}", job);
            try {
                process(job);
                log.info("job drained successfully: {}", job);
            } catch (Exception e) {
                log.error("drain failed for job {}: {}", job, e.getMessage());
            }
        }
    }
'

# PR 2 — Duplicate Logging: error logged in both consumer and caller
make_pr \
  "${DATA_REPOS}/icl-test-schematics-job-queue-manager" \
  "icl-test-schematics-job-queue-manager" \
  "feat/retry-with-logging" \
  "src/main/java/com/ibm/schematics/queue/JobConsumer.java" \
  "feat: add retry handler with escalating error logging" \
  "Adds retry logic for failed jobs. Logs the error at both the retry level and caller level." \
'

    public void retryFailed(List<Job> failed) {
        for (Job job : failed) {
            try {
                log.warn("retrying failed job: {}", job);
                process(job);
            } catch (Exception e) {
                log.error("retry failed for job: {} error: {}", job, e.getMessage());
                log.error("job details on retry failure: {}", job);
                deadLetterQueue.add(job);
            }
        }
    }
'


# ════════════════════════════════════════════════════════════════════════════════
# 8. icl-test-schematics-mon  (Go)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-mon ──"

# PR 1 — Polling Loop: logs full metric struct in collection loop
make_pr \
  "${DATA_REPOS}/icl-test-schematics-mon" \
  "icl-test-schematics-mon" \
  "feat/per-metric-collection-log" \
  "collector/collect.go" \
  "feat: add per-metric logging in health collection loop" \
  "Adds metric-level logging inside the collection loop for per-target visibility." \
'
func (c *Collector) collectAll(targets []*Target) {
	for {
		for _, t := range targets {
			m, err := c.scrape(t)
			if err != nil {
				c.log.Errorf("scrape failed for target %+v: %v", t, err)
				continue
			}
			c.log.Infof("collected metric: %+v", m)
		}
		time.Sleep(10 * time.Second)
	}
}
'

# PR 2 — JSON Blob: marshals alert struct and logs as string
make_pr \
  "${DATA_REPOS}/icl-test-schematics-mon" \
  "icl-test-schematics-mon" \
  "feat/alert-json-logging" \
  "collector/collect.go" \
  "feat: log alert payload as JSON for external audit ingestion" \
  "Serialises alert structs to JSON and logs them for downstream audit log consumers." \
'
func (c *Collector) logAlert(alert *Alert) {
	b, err := json.Marshal(alert)
	if err != nil {
		c.log.Errorf("failed to marshal alert: %v", err)
		return
	}
	c.log.Infof("alert fired: %s", string(b))
}
'


# ════════════════════════════════════════════════════════════════════════════════
# 9. icl-test-schematics-orchestrator  (Java)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-orchestrator ──"

# PR 1 — Payload Logging: logs full job object on every dispatch
make_pr \
  "${DATA_REPOS}/icl-test-schematics-orchestrator" \
  "icl-test-schematics-orchestrator" \
  "feat/dispatch-payload-logging" \
  "src/main/java/com/ibm/schematics/orchestrator/JobOrchestrator.java" \
  "feat: log full job payload on orchestrator dispatch" \
  "Logs complete job object before and after dispatch for end-to-end traceability." \
'

    public void dispatchAll(List<Job> jobs) {
        for (Job job : jobs) {
            log.info("dispatching job: {}", job);
            dispatch(job);
            log.info("job dispatched: {}", job);
        }
    }
'

# PR 2 — Duplicate Logging: validation error logged in service AND orchestrator
make_pr \
  "${DATA_REPOS}/icl-test-schematics-orchestrator" \
  "icl-test-schematics-orchestrator" \
  "feat/validation-error-reporting" \
  "src/main/java/com/ibm/schematics/orchestrator/JobService.java" \
  "feat: improve validation error visibility with detailed logging" \
  "Adds detailed validation error logging. Each constraint violation is logged at ERROR level." \
'

    public void validateAndLog(Job job) {
        if (job.getId() == null) {
            log.error("job validation failed: missing id for job {}", job);
            throw new ValidationException("missing id");
        }
        if (job.getType() == null) {
            log.error("job validation failed: missing type for job {}", job);
            throw new ValidationException("missing type");
        }
    }
'


# ════════════════════════════════════════════════════════════════════════════════
# 10. icl-test-schematics-sandbox  (Go)
# ════════════════════════════════════════════════════════════════════════════════
echo ""; echo "── icl-test-schematics-sandbox ──"

# PR 1 — Loop Logging + Payload: logs full sandbox struct during bulk provisioning
make_pr \
  "${DATA_REPOS}/icl-test-schematics-sandbox" \
  "icl-test-schematics-sandbox" \
  "feat/bulk-provision-logging" \
  "pkg/sandbox.go" \
  "feat: add BulkProvision with per-sandbox lifecycle logging" \
  "Adds BulkProvision that logs the full sandbox config before and after provisioning each one." \
'
func (m *Manager) BulkProvision(ctx context.Context, sandboxes []*Sandbox) error {
	for _, sb := range sandboxes {
		m.log.Infof("provisioning sandbox: %+v", sb)
		if err := m.provision(ctx, sb); err != nil {
			m.log.Errorf("provision failed for sandbox %+v: %v", sb, err)
			return err
		}
		m.log.Infof("sandbox ready: %+v", sb)
	}
	return nil
}
'

# PR 2 — Polling + Misclassified ERROR: polls sandbox readiness; logs timeout as ERROR
make_pr \
  "${DATA_REPOS}/icl-test-schematics-sandbox" \
  "icl-test-schematics-sandbox" \
  "feat/sandbox-readiness-poller" \
  "pkg/sandbox.go" \
  "feat: add sandbox readiness poller with timeout detection" \
  "Polls sandbox readiness after provisioning. Logs not-ready state as ERROR for alerting." \
'
func (m *Manager) WaitReady(ctx context.Context, id string) error {
	for {
		sb, err := m.store.Get(ctx, id)
		if err != nil {
			m.log.Errorf("sandbox %s not found while waiting for readiness: %v", id, err)
			return err
		}
		m.log.Infof("sandbox readiness poll: %+v", sb)
		if sb.Ready {
			return nil
		}
		time.Sleep(3 * time.Second)
	}
}
'


# ════════════════════════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════════════════════════════"
echo -e "${GREEN}Done! 20 PRs created across 10 repos.${NC}"
echo ""
echo "Anti-patterns introduced:"
echo "  Payload Logging    — icl-test-schematics-api PR1, blueprint PR1, agents PR1,"
echo "                       schematics-job PR1, orchestrator PR1, sandbox PR1"
echo "  Loop Logging       — blueprint PR1, ansible PR1, data-job PR1,"
echo "                       schematics-job PR1, queue-manager PR1, sandbox PR1"
echo "  Polling            — blueprint PR2, ansible PR2, schematics-job PR2,"
echo "                       schematics-mon PR1, sandbox PR2"
echo "  JSON Blob          — schematics-api PR2, schematics-mon PR2"
echo "  Duplicate Logging  — agents PR2, queue-manager PR2, orchestrator PR2"
echo "  Misclassified ERROR— schematics-api PR2, data-job PR2, sandbox PR2"
echo ""
echo "Verify at: https://github.com/${ORG}?tab=repositories"
echo ""
