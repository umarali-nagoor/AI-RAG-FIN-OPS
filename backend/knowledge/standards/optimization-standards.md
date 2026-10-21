---
title: Logging optimization standards
---
# Logging optimization standards

The objective is NOT to remove as many logs as possible. The objective is to generate only useful logs while preserving troubleshooting, production debugging, monitoring, alerting, security and compliance information, and to reduce unnecessary cost.

## Rule 1 - Keep function entry and exit logs
Do not recommend removal of function entry/exit logs such as "Entering createWorkspace()" and "Exiting createWorkspace()". They are useful for debugging.

## Rule 2 - Do not automatically change INFO to DEBUG
Many DEBUG logs are already disabled in production, so DEBUG and VERBOSE statements are assumed to cost nothing there. Moving an INFO log to DEBUG removes the information from production entirely; that is a deletion, not an optimisation, and needs an explicit owner decision. Prefer making the INFO log smaller, rarer or summarised.

## Rule 3 - Complete object logging (highest priority)
Logging complete objects is the most expensive pattern: `logger.info("Request {}", request)`, `logger.info("Response {}", response)`, `logger.info("Payload {}", payload)`, `logger.info("Resource {}", resourceObject)`.
Recommended: log an identifier, for example `logger.info("requestId={}", requestId)` or `logger.info("workspaceId={}", workspaceId)`.

## Rule 4 - Low-value logs
"Starting operation", "Processing event", "Finished operation" with no identifying data are low value when repeated excessively (hot paths or repeated across the codebase). Merge them into one outcome log carrying identifiers and duration.

## Rule 5 - Incorrect ERROR / CRITICAL severity
Validation failures, expected retries, resource-not-found and other recoverable conditions are not ERROR. Analyse whether they should be INFO or WARNING. INFO keeps alerting; WARNING routes to Store and search (cheapest, no alerting). CRITICAL is reserved for data loss and process-fatal conditions.

## Rule 6 - Duplicate logging
An exception logged in the service layer and logged again in the controller layer produces two records for one failure. Log once, at the boundary that handles the error; wrap and propagate in lower layers.

## Rule 7 - High-frequency logs
Loops, retry blocks, polling loops, message consumers and schedulers multiply every log line. Log a per-batch summary after a loop, log the first and last attempt of a retry, log polls only on state change or every Nth poll.

## Rule 8 - Oversized payload logging
Requests, responses, maps, structs, DTOs and JSON blobs (json.Marshal, ObjectMapper.writeValueAsString, json.dumps) serialised into log lines. Log key fields only.

## What good looks like
- Structured JSON with level, requestId, resource ids, outcome and duration.
- One log per decision or outcome, not per step.
- ERROR is actionable and unexpected.
