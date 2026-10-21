---
title: Past FinOps findings 2025 Q2
---
# Past findings (Q2 2025)

- schematics-api: full request and response objects logged in resource handlers - fixed by logging requestId and workspaceId, 18 percent volume reduction.
- schematics-job-queue-manager: consumer logged every dequeued message with the entire job spec - replaced with jobId and queue depth every 100 messages.
- schematics-agents-api: exception logged in the service layer and again in the controller layer - removed the duplicate, 7 percent reduction.
- schematics-data-job: per-row INFO logging in a batch loop - replaced by a summary after the loop, 22 percent reduction.
- Guardrail confirmed with SRE: entry/exit logs stay; DEBUG is already off in production so it is not a saving target.
