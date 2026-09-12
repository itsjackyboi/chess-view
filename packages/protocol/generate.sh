#!/usr/bin/env bash
# Regenerate schema.json from the pydantic models, then the TypeScript view of it.
# Prefers the repo virtualenv so `npm run generate` works without an activated env.
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd ../.. && pwd)"

if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="python3"
fi

"$PY" generate.py
npx --no-install json2ts \
  --input schema.json \
  --output src/generated.ts \
  --bannerComment "/* eslint-disable */
/**
 * GENERATED FILE -- do not edit.
 * Source of truth: packages/protocol/chessview_protocol/messages.py
 * Regenerate with: npm run protocol:generate
 */" \
  --style.singleQuote \
  --additionalProperties false
