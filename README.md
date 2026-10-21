# IBM Cloud Logs FinOps Copilot

> A RAG-powered tool that analyses source repositories for logging-cost anti-patterns, estimates IBM Cloud Logs spend, and provides actionable optimisation recommendations.

---

## End-to-end quick start

### Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11 | `python3.11 --version` |
| Node.js | 18+ | only needed to rebuild the UI |
| git | any | must be on `PATH` |
| gh CLI | any | for `push_all_repos.sh` |
| GitHub PAT | `repo:read` scope | for cloning private repos |

---

### Step 1 — Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```dotenv
# GitHub — required for repo cloning and PR review
FINOPS_GITHUB_TOKEN=ghp_your_token_here   # PAT with repo:read scope
FINOPS_GITHUB_ORG=umarali-nagoor

# IBM Cloud Logs — required for live cost data (optional for local dev)
IBM_CLOUD_API_KEY=your-ibm-cloud-api-key
ICL_INSTANCE_ID=your-icl-instance-guid
ICL_REGION=us-south

# LLM provider — leave as offline to run without any model
FINOPS_LLM_PROVIDER=offline
# Uncomment one of these for AI-generated narratives:
# FINOPS_LLM_PROVIDER=watsonx
# FINOPS_WATSONX_API_KEY=...
# FINOPS_WATSONX_PROJECT_ID=...
```

---

### Step 2 — Install Python dependencies

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

### Step 3 — Start the backend

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8080
```

On startup the server will:
- Load the knowledge base (standards, IBM guidance, cost reports)
- Register all 11 `icl-test-*` repos from `config/repositories.yaml`

Expected log line: `knowledge base ready`

---

### Step 4 — Clone repos from GitHub (first run or after new repos added)

With the server running, trigger a full clone + index of all 11 repos:

```bash
curl -s -X POST http://127.0.0.1:8080/api/admin/reindex \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

Or use the **⬇ Pull All Repos** button in the UI at `http://127.0.0.1:8080`.

Each repo is cloned into `backend/data/repos/icl-test-<name>/`. This only re-clones on subsequent calls if `sync=True` is set (the Pull button always sets it).

---

### Step 5 — Open the UI

```bash
open http://127.0.0.1:8080
```

| Page | What to do |
|------|-----------|
| **Repository Picker** | Click **⬇ Pull** on a repo, then **Analyze** |
| **Analyze** | See risk level, cost drivers, findings |
| **Optimize** | Ranked recommendations with code suggestions |
| **PR Review** | Paste a git diff to get PASS / WARN / BLOCK |
| **Ask** | Ask questions like *"Which repo costs most?"* |
| **Analytics** | Side-by-side cost ranking across all repos |

---

### Step 6 — Smoke test via curl

```bash
curl -s -X POST http://127.0.0.1:8080/api/analyze-repository \
  -H 'Content-Type: application/json' \
  -d '{"repository":"icl-test-schematics-api"}' | python3 -m json.tool
```

Expected: `"riskLevel": "HIGH"` with findings in `workflow/reconciler.go`.

---

### Step 7 (optional) — Sync live cost data from IBM Cloud Logs

Replace the synthetic cost reports with real billing data:

```bash
python scripts/sync_cost_reports.py \
  --instance-id  $ICL_INSTANCE_ID \
  --region       $ICL_REGION \
  --range        last_30_days \
  --out-dir      sample-data/cost-reports \
  --mapping      config/icl_applications.yaml \
  --reload-url   http://127.0.0.1:8080/api/admin/reload-cost-reports
```

The server hot-reloads the new data — no restart needed.

---

### Running via Docker (alternative to steps 2–3)

```bash
docker-compose up
```

Builds the image, mounts `sample-data/cost-reports`, and starts the server on `http://127.0.0.1:8080`. Then trigger reindex (Step 4) to clone repos.

---

### Running tests

```bash
cd backend
source .venv/bin/activate
python -m pytest           # 69 tests, ~5 s
ruff check app tests       # lint
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

## Adding repositories

Edit [`backend/config/repositories.yaml`](backend/config/repositories.yaml) — two options:

**Option A — GitHub shorthand (recommended):**
```yaml
github_repos:
  - org: umarali-nagoor
    repos:
      - name: icl-test-my-service
        description: "My service"
```

**Option B — explicit URL:**
```yaml
repositories:
  - name: my-service
    description: "My production service"
    url: https://github.com/my-org/my-service
    branch: main
```

Then trigger a reindex: `POST /api/admin/reindex` or click **⬇ Pull All Repos** in the UI.

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
