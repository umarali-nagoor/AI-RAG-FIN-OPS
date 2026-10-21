# Architecture

## Overview

The FinOps Copilot is a single-process Python/FastAPI service that:

1. **Parses source repositories** with tree-sitter (Go, Java, Python) to extract every logging call and its structural context (loop, retry, error path, etc.).
2. **Applies rule-based detectors** to classify each log statement into cost-impacting finding types (payload dumps, frequency anti-patterns, mis-classified severity, etc.).
3. **Prices the findings** against the IBM Cloud Logs billing model to estimate monthly spend and potential savings.
4. **Embeds findings and knowledge** into a local ChromaDB vector store for RAG retrieval.
5. **Generates narrative responses** via a pluggable LLM adapter (offline / OpenAI / Azure OpenAI / watsonx) grounded in the retrieved context.
6. **Serves a React SPA** from `backend/static/` over the same FastAPI process.

---

## Component map

```
finops-copilot/
├── frontend/                  React + TypeScript + Vite
│   └── src/
│       ├── pages/             One file per screen (6 screens)
│       ├── components/        Shared UI primitives
│       ├── api.ts             Typed fetch wrapper → /api/*
│       └── types.ts           TypeScript mirrors of backend Pydantic models
│
├── backend/
│   └── app/
│       ├── main.py            FastAPI app factory, route wiring, static serving
│       ├── models.py          Domain models + API request/response schemas
│       │
│       ├── api/
│       │   └── security.py    API key auth, rate limiter, security middleware
│       │
│       ├── core/
│       │   ├── config.py      Settings (pydantic-settings, FINOPS_ prefix)
│       │   ├── logging.py     Structured JSON logging configuration
│       │   └── pricing.py     IBM Cloud Logs pricing model
│       │
│       ├── ingest/
│       │   ├── extractor.py   tree-sitter AST → LogStatement extraction
│       │   ├── diff.py        Unified diff parser for PR review
│       │   └── repos.py       Git clone / pull / history (GitPython)
│       │
│       ├── analysis/
│       │   ├── analyzer.py    Orchestrates extractor → detectors → cost
│       │   ├── detectors.py   Rule-based finding detectors (rules 3–8)
│       │   ├── cost.py        LogStatement → cost estimate
│       │   ├── costreport.py  IBM Cloud Logs CSV usage report parser
│       │   └── pr.py          PR diff → PRFindingOut
│       │
│       ├── rag/
│       │   ├── chunking.py    Splits findings/docs into embeddable chunks
│       │   ├── indexer.py     Embeds and upserts chunks into ChromaDB
│       │   └── store.py       ChromaDB abstraction (EphemeralClient / PersistentClient)
│       │
│       ├── llm/
│       │   └── providers.py   Offline + OpenAI + Azure OpenAI + watsonx adapters
│       │
│       └── services/
│           ├── copilot.py     High-level: analyze / optimize / review_pr / ask
│           ├── platform.py    Repository registry, state cache, index management
│           ├── analytics.py   Cross-repository summary table
│           └── prompts.py     Prompt templates (Jinja-style strings)
│
├── deploy/
│   ├── entrypoint.sh          Binds to pod IP (never 0.0.0.0)
│   └── helm/finops-copilot/   Helm chart (Deployment, Service, PVC, Ingress)
│
├── Dockerfile                 Multi-stage: Node (frontend) → UBI9 Python 3.11
├── docker-compose.yml         Local dev / smoke testing
└── sample-data/               Synthetic repos and cost-report CSVs
```

---

## Request lifecycle

```
Browser
  │  POST /api/analyze-repository {"repository":"schematics-api"}
  ▼
FastAPI (main.py)
  │  auth middleware (X-API-Key check + rate limit)
  ▼
Copilot.analyze(repository)
  ├─ Platform.state(repository)          # cache hit or compute
  │    ├─ repos.sync()                   # git pull / clone
  │    ├─ extractor.extract_all()        # tree-sitter AST walk
  │    ├─ detectors.detect()             # rule-based classification
  │    └─ cost.estimate()                # pricing model
  │
  ├─ indexer.ensure_indexed()            # embed & upsert to ChromaDB
  │
  ├─ store.query(question)               # kNN retrieval (top-k chunks)
  │
  └─ llm.complete(prompt + context)      # offline or live LLM
       │
       ▼
  AnalyzeResponse (JSON)
```

---

## Data flow for cost estimation

1. Every `LogStatement` carries `level` (CRITICAL / ERROR / WARNING / INFO / DEBUG / VERBOSE) and context flags (`in_loop`, `in_retry`, etc.).
2. `pricing.pipeline_for(level)` maps severity → IBM Cloud Logs pipeline (Priority Insights / Analyze & Alert / Store & Search).
3. `pricing.price_per_gb(level, retention_days)` returns USD/GB for the configured retention window.
4. `cost.estimate()` multiplies ingestion volume (GB/day, from static model or calibrated from cost-report CSV) by price to get `usd_per_month`.
5. Each `Finding` carries `est_savings_pct` and `est_savings_usd_month` so the UI can rank by dollar impact.

---

## RAG retrieval

- **Chunks**: each Finding becomes one chunk; knowledge-base markdown files are split on headings.
- **Embeddings**: `local` provider uses a lightweight sentence-transformer (no network calls); `openai` / `watsonx` call the respective embedding APIs.
- **Store**: ChromaDB `PersistentClient` (disk) in production, `EphemeralClient` (in-memory) in tests.
- **Retrieval**: cosine-similarity top-k (default 6) with metadata filtering on repository name.

---

## Security design

| Concern | Mechanism |
|---|---|
| Secret storage | `pydantic.SecretStr`; `.env` file; never logged |
| Network binding | `MY_POD_IP` from Downward API; never `0.0.0.0` |
| Authentication | `X-API-Key` header; enforced in production mode |
| Rate limiting | In-process sliding-window per IP |
| Input validation | Pydantic models with `extra="forbid"` on all request bodies |
| Error disclosure | Generic error messages to clients; detail logged server-side only |
| ChromaDB CVEs | In-process only; no server endpoint; see `.pip-audit-ignore` |
