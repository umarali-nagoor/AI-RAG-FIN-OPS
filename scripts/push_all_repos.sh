#!/usr/bin/env bash
# push_all_repos.sh
# Creates (if missing) and force-pushes the full git history from
# sample-data/repos/<name> to github.com/umarali-nagoor/icl-test-<name>.
# Also pushes the AI-RAG-FIN-OPS application repo.
#
# Usage:  bash scripts/push_all_repos.sh
# Run from: repo root directory
# Prerequisite: gh CLI authenticated (gh auth login) and git installed.
set -euo pipefail

ORG="umarali-nagoor"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="$(cd "$SCRIPT_DIR/../sample-data/repos" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}  ! $*${NC}"; }
err()  { echo -e "${RED}  ✗ $*${NC}"; }

# ── helper: create repo on GitHub if it doesn't exist ────────────────────────
ensure_repo() {
  local gh_name="$1" description="$2"
  if gh api "repos/${ORG}/${gh_name}" --jq '.name' &>/dev/null; then
    warn "${gh_name} already exists on GitHub"
  else
    echo "  Creating ${gh_name}..."
    gh repo create "${ORG}/${gh_name}" --public --description "$description"
    ok "Created ${gh_name}"
    sleep 2
  fi
}

# ── helper: push FULL git history from a local repo dir to a GitHub repo ─────
# Uses a temporary remote so it never touches the repo's existing remotes.
push_history() {
  local src="$1" gh_name="$2"
  local remote_url="https://github.com/${ORG}/${gh_name}.git"

  if [ ! -d "${src}/.git" ]; then
    err "${src} has no .git directory — run scripts/init_sample_repos.sh first"
    return 1
  fi

  # Add a temporary remote (remove first if leftover from a previous run)
  git -C "${src}" remote remove _push_tmp 2>/dev/null || true
  git -C "${src}" remote add _push_tmp "${remote_url}"

  # Force-push all branches — preserves full commit history
  git -C "${src}" push _push_tmp --all --force 2>&1 | grep -v "^remote:" || true

  git -C "${src}" remote remove _push_tmp
  ok "Pushed ${gh_name} (full history)"
}

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "═══ Pushing all 11 icl-test-* sample repos (with full git history) ════"
echo ""

SAMPLE_REPOS=(
  "ai-rag-pr-sample|AI RAG pipeline — FinOps PR Review sample"
  "blueprint-api|Blueprint catalogue API"
  "schematics-agents-api|API used by Schematics agents"
  "schematics-ansible|Ansible runner wrapper"
  "schematics-api|Public Schematics REST API"
  "schematics-data-job|Nightly data export job"
  "schematics-job|Single Terraform job runner"
  "schematics-job-queue-manager|Kafka job queue consumer and scheduler"
  "schematics-mon|Health metrics collector"
  "schematics-orchestrator|Job orchestration service"
  "schematics-sandbox|Sandbox lifecycle manager"
)

for entry in "${SAMPLE_REPOS[@]}"; do
  repo="${entry%%|*}"
  desc="${entry##*|}"
  gh_name="icl-test-${repo}"
  echo ""
  echo "── ${gh_name} ──"
  ensure_repo "${gh_name}" "${desc}"
  push_history "${BASE}/${repo}" "${gh_name}"
done

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "═══ AI-RAG-FIN-OPS application repo ═══════════════════════════════════"
echo ""

APP_REPO="AI-RAG-FIN-OPS"
APP_DESC="FinOps Copilot for IBM Cloud Logs — RAG-powered log cost analysis, anti-pattern detection and PR review"

ensure_repo "${APP_REPO}" "${APP_DESC}"

# Safety check: confirm .env is gitignored before pushing the application repo
if git -C "${APP_DIR}" ls-files --others --exclude-standard | grep -q "^\.env$"; then
  err ".env would be committed — aborting for safety!"
  exit 1
fi

git -C "${APP_DIR}" remote remove _push_tmp 2>/dev/null || true
git -C "${APP_DIR}" remote add _push_tmp "https://github.com/${ORG}/${APP_REPO}.git"
git -C "${APP_DIR}" push _push_tmp --all --force 2>&1 | grep -v "^remote:" || true
git -C "${APP_DIR}" remote remove _push_tmp
ok "Pushed ${APP_REPO}"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo -e "${GREEN}All done! Verify at:${NC}"
echo "  https://github.com/${ORG}?tab=repositories"
echo ""
