#!/usr/bin/env bash
# push_all_repos.sh
# Creates (if missing) and pushes content for:
#   - 5 remaining icl-test-* sample repos
#   - 1 AI-RAG-FIN-OPS repo (full finops-copilot application)
#
# Usage:  bash scripts/push_all_repos.sh
# Run from: finops-copilot/ directory
set -euo pipefail

ORG="umarali-nagoor"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="$(cd "$SCRIPT_DIR/../sample-data/repos" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}  ! $*${NC}"; }
err()  { echo -e "${RED}  ✗ $*${NC}"; }

# ── helper: create repo if it doesn't exist ──────────────────────────────────
ensure_repo() {
  local gh_name="$1" description="$2"
  if gh api "repos/${ORG}/${gh_name}" --jq '.name' &>/dev/null; then
    warn "${gh_name} already exists on GitHub — skipping creation"
  else
    echo "  Creating ${gh_name}..."
    gh repo create "${ORG}/${gh_name}" --public --description "$description" 2>&1
    ok "Created ${gh_name}"
    sleep 2
  fi
}

# ── helper: push a local directory to a GitHub repo ──────────────────────────
push_dir() {
  local src="$1" gh_name="$2"
  local WORKDIR
  WORKDIR="$(mktemp -d)"

  cp -r "${src}/." "${WORKDIR}/"
  cd "${WORKDIR}"

  git init -b main -q
  git config user.email "finops-copilot@ibm.com"
  git config user.name "FinOps Copilot"
  git add -A
  git commit -q -m "chore: initial content"

  # force-push so re-runs are idempotent
  git remote add origin "https://github.com/${ORG}/${gh_name}.git"
  git push -u origin main --force 2>&1 | grep -v "^remote:" || true

  cd - >/dev/null
  rm -rf "${WORKDIR}"
  ok "Pushed ${gh_name}"
}

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "═══ Step 1: 5 remaining icl-test-* sample repos ═══════════════════════"
echo ""

SAMPLE_REPOS=(
  "schematics-job|ICL FinOps test repo: schematics-job"
  "schematics-job-queue-manager|ICL FinOps test repo: schematics-job-queue-manager"
  "schematics-mon|ICL FinOps test repo: schematics-mon"
  "schematics-orchestrator|ICL FinOps test repo: schematics-orchestrator"
  "schematics-sandbox|ICL FinOps test repo: schematics-sandbox"
)

for entry in "${SAMPLE_REPOS[@]}"; do
  repo="${entry%%|*}"
  desc="${entry##*|}"
  gh_name="icl-test-${repo}"
  echo ""
  echo "── ${gh_name} ──"
  ensure_repo "${gh_name}" "${desc}"
  push_dir "${BASE}/${repo}" "${gh_name}"
done

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "═══ Step 2: AI-RAG-FIN-OPS (full application) ══════════════════════════"
echo ""

APP_REPO="AI-RAG-FIN-OPS"
APP_DESC="FinOps Copilot for IBM Cloud Logs — RAG-powered log cost analysis, anti-pattern detection and PR review"

ensure_repo "${APP_REPO}" "${APP_DESC}"

# Push the finops-copilot directory — .gitignore already excludes .env,
# .venv, node_modules, __pycache__, data/, etc.
WORKDIR="$(mktemp -d)"
cp -r "${APP_DIR}/." "${WORKDIR}/"

cd "${WORKDIR}"
git init -b main -q
git config user.email "finops-copilot@ibm.com"
git config user.name "FinOps Copilot"

# Honour the project's .gitignore (double-check .env is excluded)
if git ls-files --others --exclude-standard | grep -q "^\.env$"; then
  err ".env would be committed — aborting for safety!"
  rm -rf "${WORKDIR}"
  exit 1
fi

git add -A
git commit -q -m "chore: initial release of FinOps Copilot"
git remote add origin "https://github.com/${ORG}/${APP_REPO}.git"
git push -u origin main --force 2>&1 | grep -v "^remote:" || true

cd - >/dev/null
rm -rf "${WORKDIR}"
ok "Pushed ${APP_REPO}"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo -e "${GREEN}All done! Verify at:${NC}"
echo "  https://github.com/${ORG}?tab=repositories"
echo ""
