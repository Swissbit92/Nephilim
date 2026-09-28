#!/usr/bin/env bash
# Run the tests that need a live Neo4j.
#
# WHY THIS SCRIPT EXISTS. tests/conftest.py strips the developer's prod `.env` so runs
# are hermetic -- the fix for a worktree that silently ran the wrong model. A correct
# decision with a cost: every graph test module skips in a normal `pytest tests/`,
# because each one asks for NEO4J_BASE_URL via os.getenv and finds nothing.
#
# The cost came due on 2026-09-28. ADR-017 made hard walls unsupersedable, which broke
# two supersession tests that seeded a hard wall -- and the full suite stayed green for
# days, because those tests never ran. The skip is correct; being unable to run them
# easily is what made the break invisible.
#
# Usage:  ./scripts/checks/graph_tests.sh [extra pytest args]
# Exit:   0 all passed · 1 a failure · 2 no .env or no reachable graph (NOT a pass)
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

[[ -f .env ]] || { echo "COULD NOT DETERMINE: no .env at $(pwd)" >&2; exit 2; }

NEO4J_BASE_URL="$(grep -E '^NEO4J_BASE_URL=' .env | cut -d= -f2-)"
NEO4J_PASSWORD="$(grep -E '^NEO4J_PASSWORD=' .env | cut -d= -f2-)"
NEO4J_USER="$(grep -E '^NEO4J_USER=' .env | cut -d= -f2- || true)"
export NEO4J_BASE_URL NEO4J_PASSWORD
export NEO4J_USER="${NEO4J_USER:-neo4j}"

if [[ -z "$NEO4J_BASE_URL" || -z "$NEO4J_PASSWORD" ]]; then
  echo "COULD NOT DETERMINE: NEO4J_BASE_URL or NEO4J_PASSWORD missing from .env" >&2
  exit 2
fi

MODULES=(
  tests/backend/coordinator/test_neo4j_rule_repository.py
  tests/backend/coordinator/test_identity_baseline.py
)

out=$(./.venv/bin/python -m pytest "${MODULES[@]}" -o addopts= -q "$@" 2>&1)
status=$?
echo "$out"

# A run in which everything skipped is exit 2, never 0: "nothing ran" is not "passed".
if grep -qE '^[0-9]+ skipped' <<<"$out" && ! grep -qE '[0-9]+ passed' <<<"$out"; then
  echo "COULD NOT DETERMINE: every graph test skipped — the graph was unreachable." >&2
  exit 2
fi
exit $status
