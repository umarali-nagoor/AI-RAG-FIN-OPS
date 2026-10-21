---
title: INC-2025-044 Validation failures logged as ERROR
repository: schematics-orchestrator
---
# INC-2025-044 - Validation failures flooding Priority insights

Impact: schematics-orchestrator ERROR volume grew 4x after a client released a bad template. ERROR routes to Priority insights at 1.20 to 3.25 USD per GB, and the ERROR-based alert paged on-call 40 times in one night for a caller input problem.

Root cause: invalid request and not-found conditions were logged with logger.error.

Resolution: log validation failures at WARNING (or INFO when they must stay alertable), keep ERROR for unexpected failures. Findings of this type are tracked as Rule 5.
