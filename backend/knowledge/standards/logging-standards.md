---
title: Platform logging standards (Schematics)
---
# Logging standards

## Severity guide
- CRITICAL: data loss or the process cannot continue (pages on-call). Used with process exit or fatal conditions only.
- ERROR: unexpected failure that needs a human or an automated remediation. Not validation, not-found, not retryable conditions.
- WARNING: degraded or unexpected-but-handled situations, expected client errors, retry exhaustion warnings.
- INFO: significant business events (workspace created, job completed), startup/shutdown, outcome of a batch.
- DEBUG / VERBOSE: diagnostics; assumed disabled in production.

## Required fields
Every record carries `level`, `requestId` (or `correlationId`), the primary resource id (`workspaceId`, `jobId`, ...), and `durationMs` on outcome logs.

## Forbidden in log records
- Whole request/response bodies or domain objects.
- Secrets, tokens, API keys, Terraform variable values, passwords, PII.
- Stack traces at more than one layer for the same error.

## Patterns
- Handlers log once on completion with status, ids and duration.
- Services wrap errors (`fmt.Errorf("...: %w", err)`, `raise ... from err`, `throw new ServiceException(msg, e)`) and return them; they do not also log them.
- Pollers and reconcilers log on state transition, plus a heartbeat summary at most once per minute.
- Retry helpers log the final outcome with attempt count.
- Batch loops log a summary after the loop.
