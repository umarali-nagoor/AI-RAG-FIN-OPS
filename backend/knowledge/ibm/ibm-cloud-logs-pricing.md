---
title: IBM Cloud Logs pricing and data pipelines
---
# IBM Cloud Logs pricing

IBM Cloud Logs has one pricing plan (Standard). Data is metered per gigabyte ingested. Source: IBM Cloud Docs, "Service plans and pricing".

| Consumption tier | Cost per GB (USD) |
|---|---|
| Store and search | 0.48 |
| Analyze and alert | 0.75 |
| Priority insights - 7 days retention | 1.20 |
| Priority insights - 14 days | 1.75 |
| Priority insights - 30 days | 2.25 |
| Priority insights - 60 days | 2.75 |
| Priority insights - 90 days | 3.25 |

The initial default retention in Priority insights is 7 days. Longer retention raises the Priority insights price per GB for everything routed there.

## Severity to pipeline mapping used by this platform

- CRITICAL and ERROR route to Priority insights (1.20 to 3.25 USD per GB depending on retention).
- INFO and DEBUG route to Analyze and alert (0.75 USD per GB).
- WARNING and VERBOSE route to Store and search (0.48 USD per GB).

An ERROR record is therefore 1.6x to 4.3x more expensive than an INFO record, and 2.5x to 6.8x more expensive than a WARNING record. Misclassifying an expected condition as ERROR raises cost and also creates alert noise.

## Pipelines (TCO Optimizer)

- Priority insights: logs that require immediate access and full analysis capabilities; high-severity or business-critical logs. Data is kept in hot storage.
- Analyze and alert: logs that require processing and can be queried later from an archive; monitoring and statistical analysis.
- Store and search: logs kept for compliance or post-processing that can be queried from an archive.

Alerts are only evaluated on logs in Priority insights and Analyze and alert. Alerts are never sent for logs in the Store and search pipeline. Moving a log to WARNING (Store and search) therefore removes its ability to alert; use INFO if a condition must remain alertable.
