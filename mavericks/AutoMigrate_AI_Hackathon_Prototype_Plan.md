# AutoMigrate AI — Hackathon Prototype Build Plan

## 1. Project Goal

Build a working GenAI-powered automation migration prototype that:

1. Accepts an ICAP automation asset/export.
2. Parses test cases, test data, reusable blocks, object repository and keywords.
3. Normalizes the input into a Canonical Test Model (IR).
4. Uses a GenAI agent to understand test intent and map ICAP keywords.
5. Uses a keyword knowledge base instead of allowing the LLM to invent framework APIs.
6. Generates executable Python automation code.
7. Supports **Python Playwright as the primary target**.
8. Supports **Python Selenium as a secondary/fallback target**.
9. Validates generated code.
10. Executes generated tests.
11. Performs AI-assisted repair when execution fails.
12. Produces a migration report and traceability information.
13. Provides a qTest integration adapter; for the hackathon, a mock adapter is acceptable if real qTest API access is unavailable.

### Recommended MVP scope

Focus the working demo on WEB automation keywords:

- `web_click`
- `web_setText`
- `web_getText`
- `web_assertText`
- `web_navigate`
- `web_wait`
- `web_select`

Do NOT make Windows (`win_*`) or terminal emulator (`win_TE_*`) automation part of the first working demo. Design the system so those can be added later through adapters.

---

# 2. Core Architecture

```text
                    +--------------------------+
                    |      ICAP Automation     |
                    | Test Case / Data /       |
                    | Blocks / Objects /       |
                    | Keywords                 |
                    +------------+-------------+
                                 |
                                 v
                    +--------------------------+
                    |      Input Adapter       |
                    |       ICAP Parser        |
                    | XML / JSON / CSV / File   |
                    +------------+-------------+
                                 |
                                 v
                    +--------------------------+
                    |   Canonical Test Model   |
                    |            IR            |
                    +------------+-------------+
                                 |
                                 v
             +------------------------------------------+
             |           AutoMigrate AI Agent            |
             |                                          |
             | Test Analyzer                            |
             | Keyword Resolver                         |
             | Locator Resolver                         |
             | Test Data Injector                        |
             | Code Generator                            |
             | Self-Review / Repair                      |
             +---------------------+--------------------+
                                   |
                         +---------+---------+
                         |                   |
                         v                   v
                +----------------+   +----------------+
                |   Playwright   |   |    Selenium    |
                | Python Target  |   | Python Target  |
                +--------+-------+   +--------+-------+
                         |                   |
                         +---------+---------+
                                   |
                                   v
                       +----------------------+
                       | Validation &         |
                       | Execution Engine     |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       |   qTest Adapter      |
                       | Real API / Mock      |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | Migration Report /   |
                       | Traceability / Logs  |
                       +----------------------+
```

---

# 3. Architecture Principles

## 3.1 Canonical Test Model

Do not connect the LLM directly to ICAP-specific structures and framework-specific code.

Normalize everything first.

Example:

```json
{
  "testCaseId": "TC001",
  "name": "Login Test",
  "applicationType": "WEB",
  "steps": [
    {
      "sequence": 1,
      "keyword": "web_navigate",
      "object": null,
      "value": "https://example.com"
    },
    {
      "sequence": 2,
      "keyword": "web_setText",
      "object": "username",
      "value": "${USERNAME}"
    },
    {
      "sequence": 3,
      "keyword": "web_setText",
      "object": "password",
      "value": "${PASSWORD}"
    },
    {
      "sequence": 4,
      "keyword": "web_click",
      "object": "loginButton"
    },
    {
      "sequence": 5,
      "keyword": "web_assertText",
      "object": "welcomeMessage",
      "expected": "Welcome"
    }
  ]
}
```

The AI agent consumes this normalized model.

---

# 4. Recommended Repository Structure

```text
automigrate-ai/
│
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── docker-compose.yml
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   │
│   │   ├── api/
│   │   │   ├── routes_health.py
│   │   │   ├── routes_migration.py
│   │   │   ├── routes_execution.py
│   │   │   └── routes_qtest.py
│   │   │
│   │   ├── models/
│   │   │   ├── canonical_test.py
│   │   │   ├── migration_request.py
│   │   │   ├── migration_result.py
│   │   │   └── execution_result.py
│   │   │
│   │   ├── parsers/
│   │   │   ├── base_parser.py
│   │   │   ├── icap_parser.py
│   │   │   └── json_parser.py
│   │   │
│   │   ├── knowledge/
│   │   │   ├── keyword_kb.json
│   │   │   ├── locator_rules.json
│   │   │   ├── framework_templates.json
│   │   │   └── coding_standards.json
│   │   │
│   │   ├── agents/
│   │   │   ├── migration_agent.py
│   │   │   ├── test_analyzer.py
│   │   │   ├── keyword_resolver.py
│   │   │   ├── locator_resolver.py
│   │   │   ├── code_generator.py
│   │   │   ├── validator.py
│   │   │   └── repair_agent.py
│   │   │
│   │   ├── generators/
│   │   │   ├── base_generator.py
│   │   │   ├── playwright_generator.py
│   │   │   └── selenium_generator.py
│   │   │
│   │   ├── execution/
│   │   │   ├── runner.py
│   │   │   ├── playwright_runner.py
│   │   │   └── selenium_runner.py
│   │   │
│   │   ├── qtest/
│   │   │   ├── base_adapter.py
│   │   │   ├── mock_qtest_adapter.py
│   │   │   └── qtest_adapter.py
│   │   │
│   │   └── services/
│   │       ├── migration_service.py
│   │       ├── validation_service.py
│   │       ├── execution_service.py
│   │       └── report_service.py
│   │
│   ├── tests/
│   │   ├── test_parser.py
│   │   ├── test_keyword_mapping.py
│   │   ├── test_generators.py
│   │   └── test_validation.py
│   │
│   └── requirements.txt
│
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   ├── api/
│   │   │   └── migrationApi.js
│   │   ├── components/
│   │   │   ├── UploadPanel.jsx
│   │   │   ├── MigrationPlan.jsx
│   │   │   ├── GeneratedCode.jsx
│   │   │   ├── ExecutionResult.jsx
│   │   │   ├── Traceability.jsx
│   │   │   └── Metrics.jsx
│   │   └── pages/
│   │       └── Dashboard.jsx
│   └── public/
│
├── samples/
│   ├── icap/
│   │   ├── login_test.json
│   │   ├── checkout_test.json
│   │   └── sample_testdata.json
│   └── expected/
│
├── generated/
│   ├── playwright/
│   └── selenium/
│
├── docs/
│   ├── architecture.md
│   ├── api.md
│   └── demo-script.md
│
└── scripts/
    ├── setup.ps1
    └── setup.sh
```

---

# 5. Technology Stack

Recommended hackathon stack:

## Backend

- Python 3.11+
- FastAPI
- Pydantic
- Uvicorn

## AI

Use the enterprise/approved LLM available to the team.

The LLM abstraction must be isolated behind:

```text
LLMProvider
```

so the application is not hard-coded to one provider.

Example interface:

```python
class LLMProvider:
    def generate(self, prompt: str, context: dict) -> str:
        raise NotImplementedError
```

## Frontend

Preferred:

- React
- Vite
- JavaScript/TypeScript

For a very fast prototype, Streamlit can be used instead.

## Automation

- Playwright Python
- Selenium Python
- pytest

## Knowledge Base

For MVP:

- JSON/YAML files

Do not build a complex vector database unless the hackathon specifically requires RAG.

## Packaging

- Docker
- docker-compose

---

# 6. Keyword Knowledge Base

The LLM must NOT invent Playwright/Selenium APIs.

Create `keyword_kb.json`.

Example:

```json
{
  "web_click": {
    "description": "Click a web element",
    "applicationType": "WEB",
    "playwright": {
      "python": "page.locator('{locator}').click()"
    },
    "selenium": {
      "python": "driver.find_element(By.{by}, '{value}').click()"
    }
  },
  "web_setText": {
    "description": "Enter text into a web element",
    "applicationType": "WEB",
    "playwright": {
      "python": "page.locator('{locator}').fill('{value}')"
    },
    "selenium": {
      "python": "driver.find_element(By.{by}, '{locator_value}').send_keys('{value}')"
    }
  },
  "web_getText": {
    "description": "Read text from a web element",
    "applicationType": "WEB",
    "playwright": {
      "python": "page.locator('{locator}').text_content()"
    },
    "selenium": {
      "python": "driver.find_element(By.{by}, '{locator_value}').text"
    }
  },
  "web_assertText": {
    "description": "Validate text",
    "applicationType": "WEB",
    "playwright": {
      "python": "expect(page.locator('{locator}')).to_have_text('{expected}')"
    },
    "selenium": {
      "python": "assert driver.find_element(By.{by}, '{locator_value}').text == '{expected}'"
    }
  },
  "web_navigate": {
    "description": "Navigate to URL",
    "applicationType": "WEB",
    "playwright": {
      "python": "page.goto('{value}')"
    },
    "selenium": {
      "python": "driver.get('{value}')"
    }
  },
  "web_select": {
    "description": "Select an option",
    "applicationType": "WEB",
    "playwright": {
      "python": "page.locator('{locator}').select_option('{value}')"
    },
    "selenium": {
      "python": "Select(driver.find_element(By.{by}, '{locator_value}')).select_by_value('{value}')"
    }
  }
}
```

Add more mappings only after confirming the actual ICAP semantics.

---

# 7. Canonical Test Model

Create strongly typed Pydantic models.

Example:

```python
class TestStep(BaseModel):
    sequence: int
    keyword: str
    object_name: str | None = None
    value: str | None = None
    expected: str | None = None
    metadata: dict = {}

class CanonicalTestCase(BaseModel):
    test_case_id: str
    name: str
    application_type: str
    steps: list[TestStep]
    test_data: dict = {}
    reusable_blocks: list[str] = []
    object_repository: dict = {}
```

---

# 8. Input Parser

The parser should support:

1. JSON
2. XML
3. CSV

But implement JSON first.

Create:

```python
class BaseParser:
    def parse(self, file_path: str) -> CanonicalTestCase:
        raise NotImplementedError
```

Then:

```text
JsonParser
XmlParser
CsvParser
IcapParser
```

The ICAP-specific parser should translate the ICAP structure into the Canonical Test Model.

---

# 9. AI Agent Workflow

Implement this workflow:

```text
Upload
  ↓
Parse
  ↓
Normalize
  ↓
Analyze
  ↓
Resolve Keywords
  ↓
Resolve Locators
  ↓
Inject Test Data
  ↓
Create Migration Plan
  ↓
Generate Code
  ↓
Static Validation
  ↓
Execute
  ↓
If Failure → Repair Agent
  ↓
Re-execute
  ↓
Generate Report
  ↓
qTest Adapter
```

The agent should return structured JSON rather than free-form text.

Example:

```json
{
  "applicationType": "WEB",
  "targetFramework": "PLAYWRIGHT",
  "stepsDetected": 8,
  "keywordsMapped": 8,
  "locatorsResolved": 5,
  "dataParameters": 2,
  "reusableBlocks": 1,
  "warnings": []
}
```

---

# 10. Migration Plan UI

After uploading a test, show:

```text
Migration Plan

Application Type: WEB
Target: Python Playwright

Test Steps:              8
Keywords Detected:       7
Keywords Mapped:         7
Locators Resolved:       5
Test Data Parameters:    2
Reusable Blocks:         1

Status: READY
```

Then allow:

```text
[ Generate Playwright ]
[ Generate Selenium  ]
```

---

# 11. Locator Resolution

Object repository should be treated as a separate input.

Example:

```json
{
  "username": {
    "type": "id",
    "value": "username"
  },
  "password": {
    "type": "css",
    "value": "#password"
  },
  "loginButton": {
    "type": "id",
    "value": "loginBtn"
  }
}
```

Normalize it into:

```python
Locator(
    name="username",
    strategy="id",
    value="username"
)
```

The locator resolver should:

1. Look in object repository.
2. Validate the locator format.
3. Generate target-framework syntax.
4. If execution fails, provide locator information to the repair agent.
5. Never silently invent a locator without marking it as AI-inferred.

---

# 12. Code Generation

Generate a complete executable project, not just snippets.

For Playwright:

```text
generated/playwright/
├── tests/
│   └── login_test.py
├── pages/
│   └── login_page.py
├── testdata/
│   └── login.json
├── config/
│   └── config.json
├── requirements.txt
└── pytest.ini
```

For Selenium:

```text
generated/selenium/
├── tests/
│   └── login_test.py
├── pages/
│   └── login_page.py
├── testdata/
│   └── login.json
├── config/
│   └── config.json
├── requirements.txt
└── pytest.ini
```

---

# 13. Validation Engine

Before execution, run:

## Static checks

- Python syntax
- imports
- undefined variables
- unsupported keywords
- missing locators
- duplicate steps
- missing assertions
- framework compatibility

## Validation result

```json
{
  "status": "PASS",
  "syntax": "PASS",
  "imports": "PASS",
  "keywords": "PASS",
  "locators": "PASS",
  "standards": "PASS"
}
```

---

# 14. Execution Engine

Use pytest.

For Playwright:

```bash
pytest generated/playwright/tests -v
```

For Selenium:

```bash
pytest generated/selenium/tests -v
```

Capture:

- pass/fail
- duration
- stdout
- stderr
- screenshots
- logs
- assertion errors

---

# 15. Self-Healing / Repair Agent

This is the main GenAI wow factor.

When execution fails:

```text
Execution Failure
       ↓
Error Analyzer
       ↓
Collect:
- Exception
- Failed step
- Locator
- DOM information if available
- Generated code
- Original canonical step
       ↓
Repair Agent
       ↓
Generate minimal fix
       ↓
Validate
       ↓
Re-run
```

Example:

```text
Before:
#loginBtn

Execution:
ElementNotFound

AI Analysis:
Possible locator mismatch.

Suggested:
[data-testid="login"]

Repair:
Update locator.

Re-run:
PASS
```

Never hide the repair. Show:

```text
AI Repair Applied
Reason
Old value
New value
Validation
Execution result
```

---

# 16. qTest Adapter

Create an interface:

```python
class QTestAdapter:
    def create_test_case(self, test_case):
        raise NotImplementedError

    def link_automation(self, test_case_id, script_path):
        raise NotImplementedError

    def update_execution(self, test_case_id, result):
        raise NotImplementedError
```

Implement:

```text
MockQTestAdapter
QTestAdapter
```

For hackathon demo, if real credentials/API are unavailable:

```text
Mock qTest
```

must produce realistic traceability data.

Example:

```json
{
  "icapTestCaseId": "ICAP-TC-001",
  "generatedScript": "login_test.py",
  "qTestCaseId": "QT-1001",
  "executionStatus": "PASS"
}
```

---

# 17. Traceability

Every migration should retain:

```text
ICAP Test Case
      ↕
Canonical Step
      ↕
Generated Code Line / Function
      ↕
Execution Result
      ↕
qTest Test Case
```

Example UI:

```text
ICAP-TC-001
  |
  +-- Step 1: web_navigate
  |      |
  |      +-- login_test.py:12
  |      +-- PASS
  |
  +-- Step 2: web_setText
  |      |
  |      +-- login_test.py:15
  |      +-- PASS
  |
  +-- Step 3: web_click
         |
         +-- login_test.py:18
         +-- PASS
```

---

# 18. Dashboard

Create a simple hackathon dashboard:

```text
=================================================
              AutoMigrate AI
=================================================

[ Upload ICAP Asset ]

Migration Summary
---------------------------------------------
Test Cases                 1
Steps                     12
Keywords                   7
Mapped                     7
Locators                   5
Reusable Blocks            1

Target Framework
[ Playwright ] [ Selenium ]

[ Generate ]

---------------------------------------------
Migration Status
✓ Parsed
✓ Normalized
✓ AI Analyzed
✓ Keywords Mapped
✓ Code Generated
✓ Validation Passed
✓ Execution Passed
✓ qTest Linked

---------------------------------------------
Migration Metrics

Manual Estimate:       45 min
AI Migration:           5 min
Observed Time Saving:  ~89%
---------------------------------------------

[ View Generated Code ]
[ Run Test ]
[ View Traceability ]
[ View Report ]
```

The percentage must be calculated from actual demo timings; do not hard-code an 80–90% business claim.

---

# 19. API Design

## Health

```http
GET /api/health
```

## Upload

```http
POST /api/migration/upload
```

## Analyze

```http
POST /api/migration/analyze
```

## Generate

```http
POST /api/migration/generate
```

Request:

```json
{
  "migrationId": "MIG-001",
  "targetFramework": "playwright"
}
```

## Validate

```http
POST /api/migration/validate
```

## Execute

```http
POST /api/migration/execute
```

## Repair

```http
POST /api/migration/repair
```

## Report

```http
GET /api/migration/{id}/report
```

## qTest

```http
POST /api/qtest/sync
```

---

# 20. Sample Demo Data

Create a representative login test.

Input:

```json
{
  "testCaseId": "ICAP-TC-001",
  "name": "Login Test",
  "applicationType": "WEB",
  "steps": [
    {
      "sequence": 1,
      "keyword": "web_navigate",
      "value": "https://example.com/login"
    },
    {
      "sequence": 2,
      "keyword": "web_setText",
      "object": "username",
      "value": "${USERNAME}"
    },
    {
      "sequence": 3,
      "keyword": "web_setText",
      "object": "password",
      "value": "${PASSWORD}"
    },
    {
      "sequence": 4,
      "keyword": "web_click",
      "object": "loginButton"
    },
    {
      "sequence": 5,
      "keyword": "web_assertText",
      "object": "welcomeMessage",
      "expected": "Welcome"
    }
  ]
}
```

Object repository:

```json
{
  "username": {
    "type": "id",
    "value": "username"
  },
  "password": {
    "type": "id",
    "value": "password"
  },
  "loginButton": {
    "type": "id",
    "value": "loginBtn"
  },
  "welcomeMessage": {
    "type": "id",
    "value": "welcome"
  }
}
```

Test data:

```json
{
  "USERNAME": "demo_user",
  "PASSWORD": "demo_password"
}
```

Use a safe local/demo website or mock application for execution.

---

# 21. Copilot Build Strategy

Do NOT give GitHub Copilot one giant prompt and ask it to build everything.

Build in increments.

## Prompt 1 — Repository

```text
Act as a senior Python architect.

Create the AutoMigrate AI repository according to the architecture in
docs/architecture.md.

Do not implement business logic yet.

Create:
- backend
- frontend
- parsers
- canonical models
- knowledge base
- agents
- generators
- execution
- qTest adapters
- tests
- samples

Use clean architecture and dependency injection where useful.

Do not invent ICAP APIs.
```

## Prompt 2 — Canonical Model

```text
Implement the Canonical Test Model using Pydantic.

Create models for:
- TestCase
- TestStep
- TestData
- ReusableBlock
- Locator
- MigrationPlan
- MigrationResult
- ExecutionResult

Add unit tests.

Do not implement the LLM yet.
```

## Prompt 3 — Parser

```text
Implement JSON parsing first.

Convert the sample ICAP JSON into the Canonical Test Model.

Add tests for:
- valid input
- missing fields
- unsupported keyword
- malformed JSON
```

## Prompt 4 — Knowledge Base

```text
Implement keyword_kb.json and a KeywordResolver.

Support:
web_click
web_setText
web_getText
web_assertText
web_navigate
web_select

Support Playwright and Selenium targets.

The resolver must fail explicitly for unsupported keywords.
Do not let the LLM invent framework mappings.
```

## Prompt 5 — Playwright Generator

```text
Implement the Python Playwright generator.

Input:
CanonicalTestCase + KeywordResolver + LocatorResolver.

Output:
A complete executable pytest Playwright project.

Add unit tests.
```

## Prompt 6 — Selenium Generator

```text
Implement the Selenium generator using the same Canonical Test Model.

Do not duplicate business logic.

Use the same KeywordResolver and LocatorResolver.
```

## Prompt 7 — AI Agent

```text
Implement MigrationAgent.

The agent must:
1. analyze the canonical test
2. identify application type
3. create a migration plan
4. resolve keywords
5. resolve locators
6. select target framework
7. invoke the correct generator
8. return structured JSON

Keep the LLM provider behind an interface.
```

## Prompt 8 — Validation

```text
Implement ValidationEngine.

Validate:
- Python syntax
- imports
- unresolved variables
- unsupported keywords
- missing locators
- missing assertions

Return structured validation results.
```

## Prompt 9 — Execution

```text
Implement ExecutionEngine using pytest.

Capture:
- status
- duration
- stdout
- stderr
- screenshots
- errors

Do not allow arbitrary shell commands from user input.
```

## Prompt 10 — Repair Agent

```text
Implement a RepairAgent.

Input:
- canonical test step
- generated code
- execution error
- locator information

Output:
- diagnosis
- proposed change
- repaired code
- confidence
- explanation

Only make minimal changes.
Never silently change the test intent.
```

## Prompt 11 — qTest

```text
Implement QTestAdapter interface and MockQTestAdapter.

Do not claim a real qTest integration.

The mock adapter should demonstrate:
- create test case
- link automation
- update execution
- publish result
```

## Prompt 12 — UI

```text
Build the React dashboard for AutoMigrate AI.

Pages/components:
- Upload
- Migration Analysis
- Migration Plan
- Generated Code
- Validation
- Execution
- AI Repair
- qTest Traceability
- Metrics

Use a professional enterprise hackathon design.

The UI must clearly show the complete pipeline.
```

---

# 22. Final Hackathon Demo Flow

Use exactly this flow:

```text
1. Upload ICAP sample
       ↓
2. Parse
       ↓
3. Show AI migration plan
       ↓
4. Select Playwright
       ↓
5. Generate project
       ↓
6. Show generated Python
       ↓
7. Validate
       ↓
8. Execute
       ↓
9. Show PASS
       ↓
10. Demonstrate one locator failure
       ↓
11. AI detects and repairs
       ↓
12. Re-run
       ↓
13. PASS
       ↓
14. qTest traceability
       ↓
15. Switch target → Selenium
       ↓
16. Generate Selenium version
```

---

# 23. Important Scope Boundaries

For the hackathon:

### Implement

- Web ICAP migration
- Canonical model
- Keyword KB
- Playwright
- Selenium
- AI migration planning
- AI code generation
- Static validation
- Test execution
- AI repair
- Mock qTest
- Traceability
- Dashboard

### Design but don't implement initially

- Windows automation
- Terminal emulator automation
- Full ICAP API integration
- Production qTest integration if credentials are unavailable
- Large-scale vector database
- Multi-agent complexity
- Enterprise authentication

---

# 24. Definition of Done

The prototype is considered successful when:

- [ ] User uploads sample ICAP asset.
- [ ] Parser converts it into Canonical Test Model.
- [ ] UI shows detected steps and keywords.
- [ ] Keyword KB maps all supported web keywords.
- [ ] Playwright code is generated.
- [ ] Selenium code is generated.
- [ ] Generated code passes validation.
- [ ] Generated test executes.
- [ ] Execution result is shown in UI.
- [ ] One intentional locator failure can be demonstrated.
- [ ] AI repair fixes the failure.
- [ ] Test passes after repair.
- [ ] qTest mock traceability is displayed.
- [ ] Migration report is generated.
- [ ] No unsupported ICAP capability is falsely presented as implemented.

---

# 25. Hackathon Pitch

## Title

**AutoMigrate AI — GenAI Agent for Intelligent Automation Migration**

## One-line pitch

> Upload an automation asset, and AutoMigrate AI understands the test intent, maps automation keywords and locators, generates executable Python Playwright or Selenium tests, validates and self-repairs them, and maintains end-to-end traceability.

## Strong differentiator

Traditional migration:

```text
Automation Asset
      ↓
Manual Analysis
      ↓
Manual Coding
      ↓
Manual Debugging
      ↓
Manual Execution
      ↓
Manual Traceability
```

AutoMigrate AI:

```text
Automation Asset
      ↓
AI Understanding
      ↓
Canonical Test Model
      ↓
Framework-Aware Generation
      ↓
Validation
      ↓
AI Self-Repair
      ↓
Execution
      ↓
qTest Traceability
```

---

# 26. Critical Rule for the Team

Never say:

> "AI guarantees the migrated test is correct."

Say:

> "AI accelerates migration, while deterministic validation and execution verify the generated automation."

This makes the solution technically credible.

---

# 27. First 48-Hour Implementation Priority

### Priority 1

Canonical model + sample data

### Priority 2

Keyword KB

### Priority 3

Playwright generator

### Priority 4

Execution

### Priority 5

AI migration planner

### Priority 6

Selenium generator

### Priority 7

AI repair

### Priority 8

Dashboard

### Priority 9

qTest mock

### Priority 10

Polish, metrics and presentation

Do not spend the first day on UI.

Get this working first:

```text
Sample Input
    ↓
Parser
    ↓
Canonical Model
    ↓
Keyword Mapping
    ↓
Playwright Generator
    ↓
Executable Test
    ↓
PASS
```

Once that works, add the AI and visual layers.
