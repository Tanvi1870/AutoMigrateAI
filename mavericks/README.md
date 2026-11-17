# AutoMigrate AI

**GenAI agent for intelligent automation migration** (hackathon prototype)

Upload an automation asset. AutoMigrate AI works out what the test is meant to do, maps automation keywords and locators through a controlled knowledge base, and generates executable **Python Playwright** (primary) or **Python Selenium** (fallback) tests. It then validates and runs them, repairs failures with AI help, and keeps end-to-end traceability to qTest.

> AI speeds up the migration. Deterministic validation and real test runs check the generated automation.

---

## Status

**Working end-to-end prototype.** Upload → AI analysis → migration plan → keyword mapping → Playwright/Selenium generation → static validation → real pytest execution → AI self-healing repair → mock qTest traceability → report and metrics, all driven from the React dashboard. A **DEMO MODE** switch ("Simulate Locator Failure") reproduces the repair loop on demand.

Known limits: migrations are kept in memory (lost on backend restart), the LLM defaults to an offline mock, and qTest is mocked.

- Requirements: [AutoMigrate_AI_Hackathon_Prototype_Plan.md](AutoMigrate_AI_Hackathon_Prototype_Plan.md)
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)

---

## What the MVP Covers

| Capability | MVP |
|------------|-----|
| Input | **Representative ICAP Export Format for Prototype** (JSON, our own documented format; **not** an official ICAP export), or CSV step tables. JSON supports test case, test data and object repository documents |
| Keywords | `web_navigate`, `web_click`, `web_setText`, `web_getText`, `web_assertText`, `web_wait`, `web_select` |
| Targets | Python Playwright (primary), Python Selenium (secondary) |
| AI | Migration plan narrative, AI-assisted locator repair. The LLM sits behind `LLMProvider`, with an offline mock as the default |
| Validation | Syntax, imports, undefined names, keywords, locators, variables, assertions |
| Execution | pytest against a local demo web app |
| qTest | **Mock** adapter. No real qTest integration is claimed |
| Traceability | ICAP step → canonical step → generated line → result → qTest ID |

**Not in the MVP:** `win_*` and `win_TE_*` automation (reported as future adapters, never converted), real ICAP API or export parsing, XML input, real qTest, auth, vector DB.

---

## Tech Stack

- **Backend:** Python 3.12, FastAPI, Pydantic v2, Uvicorn
- **Automation:** Playwright (Python), Selenium 4, pytest
- **AI:** pluggable `LLMProvider` (Mock / Azure OpenAI)
- **Knowledge base:** JSON files
- **Frontend:** React + Vite (needs Node.js), or a no-build HTML dashboard as a fallback

---

## Repository Layout

```text
backend/
  app/
    main.py         FastAPI app factory, CORS, 501/404 handlers
    config.py       Settings (.env)
    dependencies.py composition root (dependency injection)
    api/            routes: health, migration, execution, qtest
    models/         Canonical Test Model + plan/result/report models
    parsers/        IcapParser (format routing), JsonParser (Representative ICAP Export Format), raw format models
    knowledge/      keyword_kb.json, locator_rules.json, framework_templates.json, coding_standards.json
    agents/         analyzer, keyword/locator resolvers, data injector, migration agent, validator, repair agent
    llm/            LLMProvider, MockLLMProvider, AzureOpenAIProvider, factory
    generators/     BaseGenerator, Playwright, Selenium
    execution/      BaseRunner, Playwright, Selenium
    qtest/          BaseQTestAdapter, MockQTestAdapter, QTestAdapter (real, stub)
    storage/        MigrationStore (in-memory)
    services/       migration, validation, execution, repair, qtest, report
  tests/            backend unit/API tests
  requirements.txt
frontend/           React + Vite dashboard skeleton
samples/icap/       login_test.json, checkout_test.json, sample_testdata.json, sample_object_repository.json
generated/          playwright/ and selenium/ output (git-ignored)
tests/e2e/          reserved for end-to-end pipeline tests
docs/api.md         endpoint reference
scripts/            setup.ps1, setup.sh
```

---

## Getting Started

> Port **8001** is used because port 8000 is reserved by Windows on the development machine.

```powershell
# Creates .venv, installs the hash-locked backend deps (runtime + test suite), creates .env from .env.example
.\scripts\setup.ps1                     # add -InstallBrowsers to download Chromium

# Run the backend (demo): no auto-reload
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8001
# -> http://localhost:8001/api/health, API docs at http://localhost:8001/docs (ENVIRONMENT=local only)

# Backend development with auto-reload: watch the source only. Without --reload-dir, uvicorn watches the
# whole repository, and every generated test project (.py files under generated/) would restart the server.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8001 --reload --reload-dir backend/app

# Lint and test
.\.venv\Scripts\python.exe -m pyflakes backend\app backend\tests
cd backend; ..\.venv\Scripts\python.exe -m pytest
```

The frontend needs Node.js 18+:

```powershell
cd frontend; npm install; npm run dev    # http://localhost:5173 (proxies /api to :8001)
```

Configure the LLM and qTest in `.env`: `LLM_PROVIDER=mock|azure_openai`, `QTEST_MODE=mock|real`.

### CSV uploads

Upload [samples/icap/login_test.csv](samples/icap/login_test.csv) from the dashboard just like JSON.
CSV files must be UTF-8 (a BOM is accepted), comma-delimited, with one WEB test case per file and one step per row.

- Required columns: `testCaseId`, `testCaseName`, `sequence` (positive integer), `keyword`.
- Optional columns: `object`, `locatorType`, `locatorValue`, `value`, `expected`, `description`.
- Every row must repeat the same test case ID and name. Steps are ordered by sequence; duplicate sequences are rejected.
- Locators require all three fields: `object`, `locatorType`, `locatorValue`. Supported web types: `id`, `css`, `xpath`, `name`, `data-testid`.
- Repeat locators consistently or define each object on just one row. Conflicting definitions are rejected.
- Values are literal strings; blank cells omit the corresponding parameter. Use JSON for intentional empty-string inputs. Quote cells containing commas, quotes or newlines using standard CSV quoting.
- CSV does not define reusable blocks or test-data variables; use literal demo values, or JSON for those features.

---

## Run with Docker

### Prerequisites

- Docker Engine 24+ with **Docker Compose v2.24+** (Docker Desktop on Windows/macOS, with the WSL 2 backend on Windows).
- About 4 GB free disk space: the backend image contains Chromium for both Playwright and Selenium.
- Free host ports **8080** (dashboard) and **8001** (API).
- Internet access for the first build only (base images, pip and npm packages, browsers). Running migrations needs no internet: generated tests run against the demo app inside the backend container.

### Startup

```bash
cp .env.example .env          # optional; every setting has a safe default (mock LLM, mock qTest)
docker compose up --build
```

| URL | What |
|-----|------|
| http://localhost:8080 | Dashboard (nginx serves the React build and proxies `/api` to the backend) |
| http://localhost:8001/api/health | Backend health |
| http://localhost:8001/docs | OpenAPI docs |

The dashboard starts once the backend health check passes. Stop with `Ctrl+C`, or `docker compose down`. Generated projects and mock qTest data live in the `generated` and `data` volumes; `docker compose down -v` removes them.

### Environment variables

Set them in `.env` (read by Compose and the backend). All are optional.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_PROVIDER` | `mock` | `mock` (offline) or `azure_openai` |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` | — | Needed only for `azure_openai` (also add `openai` to `backend/requirements.txt`) |
| `LLM_TIMEOUT_SECONDS` | `30` | LLM request timeout |
| `QTEST_MODE` | `mock` | `mock` or `real` (real adapter is a stub) |
| `QTEST_BASE_URL`, `QTEST_API_TOKEN`, `QTEST_PROJECT_ID` | — | Real qTest connection |
| `EXECUTION_TIMEOUT_SECONDS` | `120` | Max time for one pytest run |
| `HEADLESS` | `true` | Must stay `true` in containers |
| `MAX_UPLOAD_BYTES` | `1048576` | Upload size limit |
| `CORS_ORIGINS` | `["http://localhost:8080", "http://localhost:5173"]` | Only needed when calling the API from another origin |
| `AM_DATA_<NAME>` | — | Overrides test data in generated tests, e.g. `AM_DATA_BASE_URL` |

Set by the backend image (no need to change): `PLAYWRIGHT_BROWSERS_PATH`, `SE_CHROMEDRIVER`, `AM_CHROME_BINARY`, `AM_BROWSER_ARGS` (`--no-sandbox --disable-dev-shm-usage` for Chrome in a container). Secrets (`*API_KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`) are never passed to generated test runs.

### Sample migration

**Dashboard:** open http://localhost:8080, drop `samples/icap/login_test.json` on the Upload page and click **⚡ Run full migration**. Switch on **DEMO MODE · Simulate Locator Failure** first to show a real failure → AI repair → PASS.

**API** (bash; in PowerShell use `curl.exe` and `\"` inside the JSON):

```bash
curl -F file=@samples/icap/login_test.json http://localhost:8001/api/migration/upload   # -> "migrationId": "MIG-001"
for step in analyze generate validate execute; do
  curl -s -X POST http://localhost:8001/api/migration/$step \
       -H 'Content-Type: application/json' -d '{"migrationId":"MIG-001","targetFramework":"playwright"}'; echo
done
curl -s -X POST http://localhost:8001/api/qtest/sync -H 'Content-Type: application/json' -d '{"migrationId":"MIG-001"}'
curl -s http://localhost:8001/api/migration/MIG-001/report
```

Use `"targetFramework":"selenium"` for the Selenium version, and add `"simulateLocatorFailure": true` to the generate call for DEMO MODE, followed by `POST /api/migration/repair` with `"apply": true`.

### Test execution

```bash
# Backend unit + API test suite, inside the running backend container
docker compose exec -w /app/backend backend python -m pytest -q -p no:cacheprovider

# Generated test projects are run by the backend itself (POST /api/migration/execute).
# To run one by hand inside the container:
docker compose exec -w /app/generated/playwright/MIG-001 backend python -m pytest -v
```

The generated projects are also in the `generated` volume, e.g. `docker compose cp backend:/app/generated ./generated-export`.

---

## Demo Flow

Upload sample → Parse → AI migration plan → Generate Playwright → Validate → Execute (PASS) → Switch the demo app to a broken locator → Execute (FAIL) → AI repair → Re-run (PASS) → qTest (mock) traceability → Generate Selenium version.
