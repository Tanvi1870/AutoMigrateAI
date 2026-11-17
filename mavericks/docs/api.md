# API

Base path: `/api`. JSON uses camelCase. Migration IDs match `^MIG-[A-Za-z0-9]{1,32}$`.
Endpoints whose feature is not built yet return **501 Not Implemented**.

| Method | Path | Request | Response | Status |
|--------|------|---------|----------|--------|
| GET | `/health` | – | `HealthResponse` | Implemented |
| POST | `/migration/upload` | multipart `file` (.json or .csv, ≤ `MAX_UPLOAD_BYTES`) | `UploadResponse` | Implemented; 415 unsupported extension, 422 invalid test file |
| POST | `/migration/analyze` | `{migrationId, targetFramework?}` | `MigrationPlan` | Stub |
| POST | `/migration/generate` | `{migrationId, targetFramework}` | `MigrationResult` | Stub |
| POST | `/migration/validate` | `{migrationId, targetFramework?}` | `ValidationResult` (`status`, `summary`, `checks[{check, label, status, reasons}]`, `issues`); 404 unknown migration, 409 not generated | Implemented |
| POST | `/migration/execute` | `{migrationId, targetFramework?}` | `ExecutionResult` (`status`, `exitCode`, `durationSeconds`, `stdout`, `stderr`, `logs`, `testsTotal`, `testsFailed`, `failedStep`, `errorType`, `errorMessage`, `screenshots`, `artifactsDir`); 404 unknown migration, 409 not generated/validated or validation failed | Implemented |
| POST | `/migration/repair` | `{migrationId, targetFramework?, apply?}` | `RepairResult` (`status`, `proposal{diagnosis, rootCause, proposedChange, oldValue, newValue, repairedCode, codeDiff, confidence, explanation}`, `validationStatus`, `reExecution`, `outcome`, `timeline`); 409 not generated/run or last run passed, 422 no safe repair | Implemented |
| GET | `/migration/{id}/repairs` | - | `RepairResult[]` repair history | Implemented |
| GET | `/migration/{id}/report` | – | `MigrationReport` | Stub |
| POST | `/qtest/sync` | `{migrationId}` | `QTestLink[]` (`sourceTestCaseId`, `generatedScript`, `qTestCaseId`, `executionStatus`, `qTestRunId`, `logs`, `screenshots`, `published`, `mock`); 409 not generated, 501 real qTest unavailable, 502 qTest error | Implemented (Mock qTest) |
| GET | `/qtest/{migrationId}/traceability` | - | `TraceabilityReport` (`qtestSystem`, `links`, per-step `steps`) | Implemented |

`targetFramework`: `playwright` (default) or `selenium`.

CSV uploads use the step-table schema documented in [CSV uploads](../README.md#csv-uploads).
They pass through the same canonical validation and migration pipeline as JSON.

Interactive docs: `http://localhost:8001/docs` while the backend is running.
