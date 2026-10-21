#!/usr/bin/env bash
# create_github_sample_repo.sh
#
# Creates the AI-RAG-PR-SAMPLE repository in your GitHub org with 4 open PRs,
# each demonstrating a different FinOps Copilot PR Review outcome.
#
# Prerequisites:
#   - gh (GitHub CLI) v2+ installed and authenticated: gh auth login
#   - GITHUB_ORG set in environment or passed as first argument
#
# Usage:
#   export GITHUB_ORG=my-org
#   bash scripts/create_github_sample_repo.sh
#
#   OR: bash scripts/create_github_sample_repo.sh my-org
#
set -euo pipefail

ORG="${1:-${GITHUB_ORG:-}}"
if [[ -z "$ORG" ]]; then
  echo "ERROR: set GITHUB_ORG or pass your org as the first argument." >&2
  exit 1
fi

REPO="AI-RAG-PR-SAMPLE"
SOURCE="$(cd "$(dirname "$0")/../sample-data/repos/ai-rag-pr-sample" && pwd)"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
CLONE="$WORKDIR/$REPO"

export GIT_AUTHOR_NAME="finops-sample"
export GIT_AUTHOR_EMAIL="finops-sample@example.invalid"
export GIT_COMMITTER_NAME="finops-sample"
export GIT_COMMITTER_EMAIL="finops-sample@example.invalid"

# -- Create repo --------------------------------------------------------------─
echo "==> Creating repo $ORG/$REPO on GitHub…"
if gh repo create "$ORG/$REPO" \
    --description "FinOps Copilot PR Review sample — logging cost anti-patterns" \
    --private 2>&1; then
  echo "    Created."
else
  echo "    Already exists or creation failed — attempting to continue with existing repo."
fi

# -- Clone --------------------------------------------------------------------─
echo "==> Cloning $ORG/$REPO into $CLONE…"
gh repo clone "$ORG/$REPO" "$CLONE"
cd "$CLONE"

# -- Seed main branch ----------------------------------------------------------
echo "==> Seeding main branch with baseline source files…"
cp -r "$SOURCE/." .
git add -A
GIT_AUTHOR_DATE="2025-08-01T10:00:00Z" GIT_COMMITTER_DATE="2025-08-01T10:00:00Z" \
  git commit -m "Initial import: AI-RAG pipeline services" --allow-empty
git push origin main

# -- Helper: create branch → patch → PR ----------------------------------------
make_pr() {
  local branch="$1"
  local pr_title="$2"
  local pr_body="$3"
  local patch_fn="$4"

  echo ""
  echo "==> PR: $pr_title  (branch: $branch)"
  git checkout -b "$branch" main
  "$patch_fn"
  git add -A
  local day
  day="$(printf '%02d' $((RANDOM % 20 + 1)))"
  GIT_AUTHOR_DATE="2025-09-${day}T09:00:00Z" \
  GIT_COMMITTER_DATE="2025-09-${day}T09:00:00Z" \
    git commit -m "$pr_title"
  git push origin "$branch"
  gh pr create \
    --base main \
    --head "$branch" \
    --title "$pr_title" \
    --body "$pr_body"
  git checkout main
}

# -- PR 1: BLOCK — object dumps inside a loop ----------------------------------
patch_block() {
cat >> services/embedding_service.go << 'GOEOF'

// BulkReindex re-indexes all documents with verbose per-item logging.
func (s *EmbeddingService) BulkReindex(ctx context.Context, docs []Document) error {
	for i, doc := range docs {
		s.logger.Infof("BulkReindex: processing item[%d]: %+v", i, doc)
		s.logger.Infof("BulkReindex: full document dump: %+v", doc)
		vec, err := s.store.Encode(ctx, doc.Content)
		if err != nil {
			s.logger.Errorf("BulkReindex: encode failed for doc %+v: %+v", doc, err)
			return err
		}
		s.logger.Infof("BulkReindex: encoded doc=%+v vec=%+v", doc, vec)
		if err := s.store.Upsert(ctx, doc.ID, vec); err != nil {
			s.logger.Errorf("BulkReindex: upsert failed doc=%+v vec=%+v err=%+v", doc, vec, err)
			return err
		}
		s.logger.Infof("BulkReindex: complete item[%d] doc=%+v", i, doc)
	}
	return nil
}
GOEOF
}

make_pr \
  "feat/bulk-reindex" \
  "feat: add BulkReindex for full corpus re-indexing" \
  "Adds BulkReindex to re-index the full document corpus.

⚠ Expected FinOps result: **BLOCK** — logs full object dumps (\`%+v\`) on every item inside a loop." \
  patch_block

# -- PR 2: WARN — retry logging on every attempt ------------------------------─
patch_warn_retry() {
cat >> handlers/resource_handler.go << 'GOEOF'

// SyncWithDetailedRetry syncs a resource with per-attempt verbose logging.
func (h *ResourceHandler) SyncWithDetailedRetry(ctx context.Context, id string) error {
	for attempt := 0; attempt < 5; attempt++ {
		state, _ := h.service.GetState(ctx, id)
		h.logger.Infof("SyncWithDetailedRetry: attempt=%d id=%s currentState=%+v", attempt, id, state)
		err := h.service.Sync(ctx, id)
		if err == nil {
			h.logger.Infof("SyncWithDetailedRetry: success attempt=%d id=%s state=%+v", attempt, id, state)
			return nil
		}
		h.logger.Errorf("SyncWithDetailedRetry: failed attempt=%d id=%s err=%v state=%+v", attempt, id, err, state)
	}
	return fmt.Errorf("sync failed after 5 attempts")
}
GOEOF
}

make_pr \
  "fix/sync-retry-logging" \
  "fix: improve sync retry observability" \
  "Improves retry logging so each attempt is traceable.

⚠ Expected FinOps result: **WARN** — logs on every retry attempt including full state object dumps." \
  patch_warn_retry

# -- PR 3: WARN — polling log inside tight loop --------------------------------
patch_warn_poll() {
cat >> retrieval/retriever.go << 'GOEOF'

// ContinuousHealthCheck polls the embedding store health continuously.
func (r *Retriever) ContinuousHealthCheck(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		default:
			result, err := r.embedder.Query(ctx, "ping", 1)
			if err != nil {
				r.logger.Errorf("ContinuousHealthCheck: ping failed err=%v", err)
			} else {
				r.logger.Infof("ContinuousHealthCheck: ok result=%+v", result)
			}
			r.logger.Infof("ContinuousHealthCheck: cycle complete result=%+v err=%v", result, err)
		}
	}
}
GOEOF
}

make_pr \
  "feat/health-check-polling" \
  "feat: add continuous embedding store health check" \
  "Adds a continuous health check that polls the embedding store.

⚠ Expected FinOps result: **WARN** — logs on every poll cycle inside a tight loop." \
  patch_warn_poll

# -- PR 4: PASS — clean structured logging ------------------------------------─
patch_pass() {
cat >> ingestion/ingester.go << 'GOEOF'

// IngestWithMetadata ingests a document and records structured metadata.
func (ing *Ingester) IngestWithMetadata(ctx context.Context, doc Document, sourceID string) error {
	if doc.Content == "" {
		ing.logger.Errorf("IngestWithMetadata: empty content doc_id=%s source_id=%s", doc.ID, sourceID)
		return ErrInvalidDocument
	}
	if err := ing.queue.Publish(ctx, doc); err != nil {
		ing.logger.Errorf("IngestWithMetadata: publish failed doc_id=%s source_id=%s err=%v", doc.ID, sourceID, err)
		return err
	}
	ing.logger.Infof("IngestWithMetadata: queued doc_id=%s source_id=%s", doc.ID, sourceID)
	return nil
}
GOEOF
}

make_pr \
  "refactor/structured-ingest-logging" \
  "refactor: use structured key=value logging in ingestion" \
  "Replaces object dumps with structured key=value log fields.

✅ Expected FinOps result: **PASS** — logs only scalar identifiers, no object dumps, no loop logging." \
  patch_pass

# -- Done ----------------------------------------------------------------------
echo ""
echo "✅ Done!  https://github.com/$ORG/$REPO"
echo ""
echo "Next steps:"
echo "  1. Update backend/config/repositories.yaml — replace the path: entry with:"
echo "       - name: ai-rag-pr-sample"
echo "         description: AI RAG pipeline — FinOps PR Review sample"
echo "         url: https://github.com/$ORG/$REPO"
echo "         branch: main"
echo "  2. Add to .env:"
echo "       FINOPS_GITHUB_ORG=$ORG"
echo "       FINOPS_GITHUB_TOKEN=<your-token>"
echo "  3. Restart the backend and open the PR Review tab"
