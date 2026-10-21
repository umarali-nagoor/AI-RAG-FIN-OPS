#!/usr/bin/env bash
# Initialises each sample service as a git repository with a short history so that history-based analytics
# (recently introduced costly patterns) work. Safe to re-run: existing .git directories are replaced.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../sample-data/repos" && pwd)"
export GIT_AUTHOR_NAME="sample" GIT_AUTHOR_EMAIL="sample@example.invalid" GIT_COMMITTER_NAME="sample" GIT_COMMITTER_EMAIL="sample@example.invalid"

# Idempotent: undo the later "history" edits (below) before taking the baseline snapshot.
perl -0pi -e 's/\n+func \(r \*Reconciler\) watchJobs.*\z/\n/s' "$ROOT/schematics-api/workflow/reconciler.go"
perl -pi -e 's/depth=\{\} items"/depth={}"/' "$ROOT/schematics-job-queue-manager/src/main/java/com/ibm/schematics/queue/JobConsumer.java"

for dir in "$ROOT"/*/; do
  name="$(basename "$dir")"
  rm -rf "$dir/.git"
  git -C "$dir" init -q -b main
  git -C "$dir" add -A
  GIT_AUTHOR_DATE="2025-05-02T10:00:00Z" GIT_COMMITTER_DATE="2025-05-02T10:00:00Z" \
    git -C "$dir" commit -q -m "Initial import of $name"
done

# A later commit in schematics-api introduces a costly polling log (mirrors INC-2025-031).
api="$ROOT/schematics-api"
cat >> "$api/workflow/reconciler.go" <<'GO'

func (r *Reconciler) watchJobs(ctx context.Context, jobs []*Job) {
	for {
		for _, job := range jobs {
			r.logger.Infof("watching job %+v", job)
		}
		time.Sleep(time.Second)
	}
}
GO
git -C "$api" add -A
GIT_AUTHOR_DATE="2025-06-10T09:30:00Z" GIT_COMMITTER_DATE="2025-06-10T09:30:00Z" \
  git -C "$api" commit -q -m "Add job watcher to reconciler"

# A later, harmless commit in schematics-job-queue-manager.
q="$ROOT/schematics-job-queue-manager"
perl -pi -e 's/depth=\{\}"/depth={} items"/' "$q/src/main/java/com/ibm/schematics/queue/JobConsumer.java"
git -C "$q" add -A
GIT_AUTHOR_DATE="2025-06-12T09:30:00Z" GIT_COMMITTER_DATE="2025-06-12T09:30:00Z" \
  git -C "$q" commit -q -m "Tweak queue depth log wording"
echo "Initialised sample repositories under $ROOT"
