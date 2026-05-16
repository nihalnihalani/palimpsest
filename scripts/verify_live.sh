#!/usr/bin/env bash
# Palimpsest — live verification harness.
# Run this AFTER docker compose up -d and after setting GEMINI_API_KEY in .env.
# Exits non-zero on any failure so you know exactly where the wheels come off.
set -uo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source .venv/bin/activate

PASS=0
FAIL=0
log_pass() { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
log_fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
section() { printf "\n\033[1m== %s ==\033[0m\n" "$1"; }

section "1. Redis reachable"
# Use Python so it works against docker, brew, OR Redis Cloud (any REDIS_URL).
REDIS_PING=$(python -c "
import os, sys
from dotenv import load_dotenv
load_dotenv()
import redis
try:
    r = redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379'),
                             decode_responses=True, socket_connect_timeout=3)
    print('PONG' if r.ping() else 'FAIL')
except Exception as e:
    print(f'FAIL: {e}')
    sys.exit(1)
" 2>&1)
if echo "$REDIS_PING" | grep -q "^PONG"; then
    log_pass "Redis PONG ($REDIS_URL_HINT)"
else
    log_fail "Redis not responding: $REDIS_PING"
    log_fail "Set REDIS_URL in .env to a working endpoint (local or cloud)"
    exit 1
fi
# Verify RedisJSON module is loaded (required for our wiki:concept:* state)
JSON_OK=$(python -c "
import os, redis
from dotenv import load_dotenv
load_dotenv()
r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
try:
    r.json().set('_probe', '$', {'ok': True})
    r.delete('_probe')
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
" 2>&1)
if echo "$JSON_OK" | grep -q "^OK"; then
    log_pass "RedisJSON module present"
else
    log_fail "RedisJSON unavailable: $JSON_OK"
    log_fail "You need Redis Stack (not plain Redis). For cloud, ensure RedisJSON is enabled."
    exit 1
fi

section "2. .env loaded"
if grep -q "^GEMINI_API_KEY=." .env 2>/dev/null; then
    log_pass ".env has GEMINI_API_KEY"
else
    log_fail ".env missing or GEMINI_API_KEY empty — fill it in"
    exit 1
fi

section "3. hello_redis"
if python scripts/hello_redis.py 2>&1 | grep -q "JSON.GET ->"; then
    log_pass "Redis streams + JSON roundtrip"
else
    log_fail "hello_redis failed"
fi

section "4. hello_gemini"
GEMINI_OUT=$(python scripts/hello_gemini.py 2>&1 | tail -1)
if echo "$GEMINI_OUT" | grep -qi "hello"; then
    log_pass "Gemini 3 reachable: ${GEMINI_OUT}"
else
    log_fail "hello_gemini failed: ${GEMINI_OUT}"
fi

section "5. hello_cognee (~30-60s)"
if python scripts/hello_cognee.py 2>&1 | grep -q "GRAPH_COMPLETION"; then
    log_pass "Cognee add → cognify → search works"
else
    log_fail "hello_cognee failed — check LLM_* env vars in .env"
fi

section "6. Reset + seed (~60-120s)"
wiki reset 2>&1 | tail -1
SEED_OUT=$(wiki seed 2>&1 | tail -2)
echo "$SEED_OUT"
N_CONCEPTS=$(ls wiki/concepts/*.md 2>/dev/null | wc -l | tr -d ' ')
if [ "$N_CONCEPTS" -ge 3 ]; then
    log_pass "Seeded ${N_CONCEPTS} concept pages"
    echo "Concept pages:"
    ls wiki/concepts/ | sed 's/^/    /'
else
    log_fail "Only ${N_CONCEPTS} concept pages — extract_concepts fallback may be misfiring"
fi

section "7. Hero moment (canned contradiction)"
wiki inject-canned contradiction_1 2>&1 | tail -1
wiki ingest --once 2>&1 | tail -3
SUPERSEDES_OUT=$(wiki graph supersedes 2>&1)
echo "$SUPERSEDES_OUT"
if echo "$SUPERSEDES_OUT" | grep -qE "source=|src="; then
    log_pass "SUPERSEDES edge written + readable"
elif echo "$SUPERSEDES_OUT" | grep -q "no SUPERSEDES"; then
    log_fail "No SUPERSEDES edge — check query.self_improve and cognee_io.list_supersedes"
else
    log_fail "Unexpected supersedes output"
fi

section "8. Eval (held-out 0/3 → 3/3)"
EVAL_OUT=$(wiki eval 2>&1)
echo "$EVAL_OUT"
SCORE=$(echo "$EVAL_OUT" | grep -oE "Score: [0-9]/3" | head -1 || echo "")
if echo "$SCORE" | grep -qE "[23]/3"; then
    log_pass "Eval ${SCORE}"
else
    log_fail "Eval too low: ${SCORE:-no score}"
fi

section "9. Lint report"
LINT_OUT=$(wiki lint 2>&1 | head -3)
echo "$LINT_OUT"
if echo "$LINT_OUT" | grep -q "wrote"; then
    log_pass "Lint report written"
else
    log_fail "Lint failed"
fi

section "10. Vector-smoke (cognee resolved provider)"
VSMOKE_OUT=$(wiki vector-smoke 2>&1 | tail -10)
if echo "$VSMOKE_OUT" | grep -qE "resolved vector provider: (lancedb|pgvector|chromadb|neptune_analytics|redis)"; then
    log_pass "Vector-smoke wrote docs/evidence/vector_provider.json"
else
    log_fail "Vector-smoke produced no provider line"
fi

section "11. Skill loop status (cognee 1.x SkillRunEntry path)"
STATUS_OUT=$(wiki improve --status 2>&1 | tail -10)
if echo "$STATUS_OUT" | grep -q "ingested_skills"; then
    log_pass "wiki improve --status reachable"
else
    log_fail "wiki improve --status failed"
fi

echo
printf "\033[1mRESULT: %d passed, %d failed\033[0m\n" "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo "Demo NOT ready. Fix failures above before rehearsing."
    exit 1
fi
echo "Demo READY. Snapshot the state now:"
echo "  tar czf snapshot/demo-baked.tar.gz wiki/ .cognee_system/ .data_storage/ 2>/dev/null || tar czf snapshot/demo-baked.tar.gz wiki/"
