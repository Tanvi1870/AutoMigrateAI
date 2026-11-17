#!/usr/bin/env bash
# Sets up the AutoMigrate AI backend. Pass --install-browsers to download Chromium for Playwright.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.lock
if [ "${1:-}" = "--install-browsers" ]; then
  .venv/bin/python -m playwright install chromium
fi
[ -f .env ] || cp .env.example .env
echo "Setup complete. Run: source .venv/bin/activate"
