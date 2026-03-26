#!/bin/bash
# Start the BYOA agent runtime
cd "$(dirname "$0")"

export MATRIX_HOMESERVER="${MATRIX_HOMESERVER:-http://localhost:8008}"
export AGENTS_DIR="${AGENTS_DIR:-./agents}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "Warning: ANTHROPIC_API_KEY not set. Agents won't be able to think."
  echo "  export ANTHROPIC_API_KEY=sk-..."
  echo ""
fi

python -m agent_runtime.main
