---
title: INC-2025-031 Reconciler polling log storm
repository: schematics-api
---
# INC-2025-031 - Logging cost spike after reconciler release

Impact: schematics-api IBM Cloud Logs volume rose from about 150 GB/day to about 320 GB/day within two days of a reconciler change, increasing Analyze and alert spend by roughly 3,800 USD per month.

Root cause: a new reconciliation loop logged "polling workspace status" at INFO on every 2 second tick for every active workspace and included the full workspace resource object.

Resolution: log only on status transitions plus one summary per minute; replace the resource object with workspaceId and phase. Volume returned to baseline. Lesson: any INFO log inside a polling loop must be reviewed in PR.
