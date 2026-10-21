---
title: IBM Cloud Logs cost control guidance
---
# Controlling IBM Cloud Logs cost

Source: IBM Cloud Docs "Controlling cost", "Configuring the TCO Optimizer" and "Controlling data ingested for search".

## Code-level controls (this copilot focuses here)

- Data is metered by gigabytes ingested, so the number and size of log records emitted by the application determine the bill.
- Include an appropriate `level`, `loglevel` or `severity` value in every JSON log record. TCO policies use severity to choose the pipeline, so consistent severities give consistent routing.
- Send logs as JSON. Extract frequently searched fields (for example `http_error_code`) into separate keys instead of embedding them in a long message line.
- For non-Kubernetes sources include `application` and `subsystemName` fields; TCO processing uses them.
- Do not log complete request, response or domain objects. Log identifiers (requestId, workspaceId) and look the object up when troubleshooting.

## Platform-level controls (operations teams)

- TCO policies route logs by application name, subsystem name and severity to Priority insights, Analyze and alert, Store and search, or block them. Policies are applied at ingestion and the first matching policy wins; blocking policies must be defined last.
- Application and policy overrides allow a different priority per severity inside an application-subsystem pair.
- Parsing rules with the `block` option drop logs by RegEx; blocked logs can optionally be kept in Store and search.
- Reduce Priority insights retention when long hot retention is not needed.
- Logging agent filters (Fluent Bit grep exclude, modify filter, `excludeLogSourcePaths`, Lua scripts) remove data before ingestion.
- Data usage metrics (`cx_data_usage_bytes_total`, labels application_name, subsystem_name, severity, priority) and the Data Usage Analyzer show GB sent per application and severity; use them to calibrate estimates.

## Guidance for this copilot

Static analysis cannot see runtime volume. Treat estimates as ranges, calibrate them with the data usage report for the service, and prefer fixing the code (smaller, fewer, correctly-leveled records) over blocking data, because blocked logs cannot be recovered.
