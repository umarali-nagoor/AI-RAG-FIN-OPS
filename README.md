# IBM Cloud Logs FinOps Copilot

> A RAG-powered tool that analyses source repositories for logging-cost anti-patterns, estimates IBM Cloud Logs spend, and provides actionable optimisation recommendations.

---

## Quick start (local, offline mode)

```bash
# 1. Clone and prepare the environment
cd finops-copilot/backend
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Seed sample repositories (synthetic data, safe to re-run)
../scripts/init_sample_repos.sh

# 3. Run the API server
FINOPS_COST_REPORTS_DIR=../sample-data/cost-reports \
  .venv/bin/uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8080

# 4. Open the UI
open http://127.0.0.1:8080
# or use the Swagger docs at http://127.0.0.1:8080/docs
```

### Quick smoke test

```bash
curl -s -X POST http://127.0.0.1:8080/analyze-repository \
  -H 'Content-Type: application/json' \
  -d '{"repository":"schematics-api"}' | python3 -m json.tool
```

---

## Features

| Screen | Description |
|---|---|
| Repository Picker | List all configured repositories; launch analyse or optimise flows |
| Root-Cause Analysis | Identifies the dominant cost drivers in a repository |
| Optimization Table | Ranked recommendations with estimated savings and suggested code |
| PR Diff Review | Reviews a unified diff for new logging cost risk; returns PASS / WARN / BLOCK |
| Ask | Conversational interface over the RAG knowledge base |
| Platform Ranking | Side-by-side cost and risk comparison across all repositories |

---

## Architecture overview

```
browser ──▶ React SPA (Vite, TypeScript)
               │ /api/*
               ▼
          FastAPI (Python 3.11)
          ├── Copilot service  ─▶ LLM provider (offline / OpenAI / watsonx)
          ├── Platform service ─▶ Git repos (GitPython)
          ├── RAG layer        ─▶ ChromaDB (in-process, EphemeralClient / PersistentClient)
          ├── Cost model       ─▶ IBM Cloud Logs pricing (pricing.py)
          └── Static files     ◀─ frontend build output (backend/static/)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for a deeper dive.

---

## Configuration

All settings are environment variables with the `FINOPS_` prefix. A `.env` file in the working directory is loaded automatically (never commit it).

| Variable | Default | Description |
|---|---|---|
| `FINOPS_ENVIRONMENT` | `development` | `production` enables API key enforcement |
| `FINOPS_API_KEYS` | *(empty)* | Comma-separated API keys; required in production |
| `FINOPS_LLM_PROVIDER` | `offline` | `offline` \| `openai` \| `azure_openai` \| `watsonx` |
| `FINOPS_COST_REPORTS_DIR` | `../sample-data/cost-reports` | Path to IBM Cloud Logs usage export CSV files |
| `FINOPS_DATA_DIR` | `./data` | Where ChromaDB persists its vector store |
| `FINOPS_REPOS_CONFIG` | `config/repositories.yaml` | YAML list of repository definitions |

For the full list see [`backend/app/core/config.py`](backend/app/core/config.py).

### LLM integration

Set `FINOPS_LLM_PROVIDER` and the matching credentials in `.env`:

```dotenv
# watsonx
FINOPS_LLM_PROVIDER=watsonx
FINOPS_WATSONX_API_KEY=<your-ibm-cloud-api-key>
FINOPS_WATSONX_PROJECT_ID=<your-project-id>

# OpenAI
FINOPS_LLM_PROVIDER=openai
FINOPS_OPENAI_API_KEY=sk-...
```

---

## Adding real repositories

Edit [`backend/config/repositories.yaml`](backend/config/repositories.yaml):

```yaml
repositories:
  - name: my-service
    description: "My production microservice"
    url: "https://github.com/my-org/my-service"
    branch: main
```

Place IBM Cloud Logs usage export CSV files in `FINOPS_COST_REPORTS_DIR` (one file per repository, named `<repository>.csv`).

---

## Running tests

```bash
cd backend
.venv/bin/python -m pytest           # 65 tests, ~5 s
.venv/bin/ruff check app tests       # lint
.venv/bin/pip-audit -r requirements.txt \
  --ignore-vuln PYSEC-2026-311 \
  --ignore-vuln PYSEC-2026-3813 \
  --ignore-vuln PYSEC-2026-3814 \
  --ignore-vuln PYSEC-2026-3815     # see .pip-audit-ignore for rationale
```

---

## Deployment

### Docker

```bash
# Build
docker build -t finops-copilot:1.0.0 .

# Run (development)
docker-compose up
```

### Kubernetes (Helm)

```bash
# Create the secret first
kubectl create secret generic finops-secrets \
  --from-literal=FINOPS_API_KEYS=<your-key>

# Install
helm install finops-copilot ./deploy/helm/finops-copilot \
  --set image.repository=registry.example.com/finops-copilot \
  --set existingSecret=finops-secrets

# Lint / dry-run
helm lint ./deploy/helm/finops-copilot
helm template finops-copilot ./deploy/helm/finops-copilot \
  --set image.repository=registry.example.com/finops-copilot
```

See [`deploy/helm/finops-copilot/values.yaml`](deploy/helm/finops-copilot/values.yaml) for all chart options.

---

## Security notes

- The API **never** binds to `0.0.0.0`. In containers the entrypoint reads `MY_POD_IP` (Kubernetes Downward API) and falls back to the first non-loopback address.
- In `production` mode all endpoints require an `X-API-Key` header. Keys must be at least 24 characters.
- ChromaDB is used in **in-process mode only** (no server). The published CVEs for chromadb ≥ 1.0.0 require a network-accessible ChromaDB server endpoint; they do not apply here (see [`backend/.pip-audit-ignore`](backend/.pip-audit-ignore)).
- Secrets are loaded from environment variables or a `.env` file and are never logged.

---

## License

See `LICENSE` file (to be added).
