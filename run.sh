#!/usr/bin/env bash
# Palimpsest orchestrator. One script to rule them all.
set -euo pipefail
cd "$(dirname "$0")"

# --- ANSI ---
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
    C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_CYAN=$'\033[36m'
else
    C_RESET=""; C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""
fi
info() { printf "%s%s%s\n" "$C_CYAN" "$*" "$C_RESET"; }
ok()   { printf "%s✓ %s%s\n" "$C_GREEN" "$*" "$C_RESET"; }
warn() { printf "%s⚠ %s%s\n" "$C_YELLOW" "$*" "$C_RESET"; }
fail() { printf "%s✗ %s%s\n" "$C_RED" "$*" "$C_RESET" >&2; }
step() { printf "\n%s%s== %s ==%s\n" "$C_BOLD" "$C_CYAN" "$*" "$C_RESET"; }

# --- helpers ---
require_cmd() { command -v "$1" >/dev/null || { fail "missing command: $1"; exit 1; }; }
have_venv()   { [ -f .venv/bin/activate ]; }
activate_venv() {
    if ! have_venv; then fail ".venv not found -- run: ./run.sh setup"; exit 1; fi
    set +u
    # shellcheck disable=SC1091
    source .venv/bin/activate
    set -u
}
require_env_key() {
    if ! [ -f .env ]; then fail "missing .env (run: ./run.sh setup)"; exit 1; fi
    if ! grep -q '^GEMINI_API_KEY=..*' .env; then
        fail "GEMINI_API_KEY is empty in .env -- edit it and re-run"
        exit 1
    fi
}

# --- subcommands ---
cmd_setup() {
    step "setup"
    require_cmd python3.11

    # Redis runtime: cloud (URL points off-host) OR local (docker / brew).
    # We detect by inspecting REDIS_URL in .env, if present.
    local redis_url=""
    if [ -f .env ]; then
        redis_url=$(grep -E "^REDIS_URL=" .env | head -1 | cut -d= -f2-)
    fi
    if echo "$redis_url" | grep -qE "@.+\.(com|net|io|cloud)"; then
        ok "REDIS_URL points to remote/cloud -- skipping local Redis bringup"
    elif command -v redis-stack-server >/dev/null; then
        if pgrep -f "redis-stack-server" >/dev/null; then
            ok "redis-stack-server already running (brew)"
        else
            info "starting redis-stack-server in background..."
            redis-stack-server --daemonize yes
        fi
    elif command -v docker >/dev/null && docker info >/dev/null 2>&1; then
        if docker compose ps --status running 2>/dev/null | grep -q wiki-redis; then
            ok "redis already up (docker)"
        else
            info "starting docker compose..."
            docker compose up -d
            for i in $(seq 1 10); do
                if docker exec wiki-redis redis-cli PING 2>/dev/null | grep -q PONG; then
                    ok "redis PONG"
                    break
                fi
                sleep 1
            done
        fi
    else
        warn "no local Redis available (neither redis-stack-server nor docker)."
        warn "either install one OR set REDIS_URL in .env to a cloud Redis URL."
    fi

    if have_venv; then
        ok ".venv exists"
    else
        info "creating .venv..."
        python3.11 -m venv .venv
        activate_venv
        pip install --quiet -e .
        ok "venv ready"
    fi
    activate_venv
    pip install --quiet -e . >/dev/null   # idempotent

    if [ -f .env ]; then
        ok ".env exists"
    else
        info "copying .env.example -> .env"
        cp .env.example .env
        warn "edit .env and set GEMINI_API_KEY before continuing"
    fi
}

cmd_doctor() {
    activate_venv
    wiki doctor
}

cmd_verify() {
    activate_venv
    require_env_key
    bash scripts/verify_live.sh
}

cmd_seed() {
    activate_venv
    require_env_key
    step "reset + seed + load-baseline"
    wiki reset
    wiki seed
    wiki load-baseline
    ok "wiki populated"
    step "post-seed doctor"
    if ! wiki doctor; then
        fail "doctor reported failures after seed -- fix before demo"
        return 1
    fi
}

cmd_demo() {
    activate_venv
    require_env_key
    step "running demo flow"
    bash demo/run_demo.sh
}

cmd_rethink() {
    activate_venv
    require_env_key
    step "wiki rethink (memify-style enrichment)"
    wiki rethink
}

cmd_test() {
    activate_venv
    step "pytest"
    pytest -q tests/
}

cmd_vector_smoke() {
    activate_venv
    step "wiki vector-smoke (verify cognee's resolved vector backend)"
    wiki vector-smoke
}

cmd_evidence() {
    activate_venv
    require_env_key
    local label="${2:-baseline}"
    local runs="${3:-5}"
    step "wiki evidence --label $label --runs $runs"
    wiki evidence --label "$label" --runs "$runs"
}

cmd_improve() {
    activate_venv
    require_env_key
    step "skill self-improvement loop (cognee 1.x SkillRunEntry -> improve_skill)"
    info "1/4  remember ./my_skills into cognee"
    wiki improve --remember
    info "2/4  exercise the code-review skill"
    wiki improve --run code-review --prompt "Review the latest concept rewrite for accuracy and citation integrity."
    info "3/4  record a low score -> propose a SKILL.md rewrite"
    wiki improve --record code-review --score 0.3 --task-text "Reviewed agent-memory rewrite; missing citations."
    info "4/4  current status"
    wiki improve --status
    info "to apply the proposal: ./run.sh improve-apply <proposal_id>"
}

cmd_improve_apply() {
    activate_venv
    require_env_key
    if [ $# -lt 2 ]; then
        fail "usage: ./run.sh improve-apply <proposal_id>"
        exit 2
    fi
    step "wiki improve --apply $2"
    wiki improve --apply "$2"
}

cmd_all() {
    cmd_setup
    require_env_key
    cmd_doctor || { fail "doctor failed -- fix before continuing"; exit 1; }
    cmd_verify
    cmd_vector_smoke
    cmd_seed
    cmd_demo
}

cmd_help() {
    cat <<HELP
Palimpsest orchestrator

Usage: ./run.sh <subcommand>

  setup          Idempotent: venv + pip install + Redis bringup (auto: cloud/brew/docker) + .env scaffold
  doctor         Diagnostic dump (Redis health, Cognee graph stats, env, logs)
  verify         Run scripts/verify_live.sh (9-step live smoke) + wiki vector-smoke
  seed           wiki reset && wiki seed && wiki load-baseline
  demo           Run demo/run_demo.sh (3-min stage flow)
  rethink        wiki rethink (cognee.memify graph enrichment)
  vector-smoke   wiki vector-smoke (probe cognee's resolved vector backend, write docs/evidence/)
  evidence       wiki evidence --label <baseline|improved> --runs <N>   (default: baseline, 5)
  improve        Run the full skill self-improvement loop demo (remember -> run -> record -> status)
  improve-apply  ./run.sh improve-apply <proposal_id>  (commit a previously-proposed SKILL.md rewrite)
  test           pytest -q tests/
  all            setup -> doctor -> verify -> vector-smoke -> seed -> demo
  help           This message

Examples:
  ./run.sh setup                       # one-time
  ./run.sh all                         # full pipeline
  ./run.sh evidence baseline 5         # capture before-state eval
  ./run.sh seed                        # ingest canned items
  ./run.sh evidence improved 5         # capture after-state eval
  ./run.sh improve                     # exercise the SkillRunEntry loop
HELP
}

# --- main ---
case "${1:-help}" in
    setup)         cmd_setup ;;
    doctor)        cmd_doctor ;;
    verify)        cmd_verify ;;
    seed)          cmd_seed ;;
    demo)          cmd_demo ;;
    rethink)       cmd_rethink ;;
    vector-smoke)  cmd_vector_smoke ;;
    evidence)      cmd_evidence "$@" ;;
    improve)       cmd_improve ;;
    improve-apply) cmd_improve_apply "$@" ;;
    test)          cmd_test ;;
    all)           cmd_all ;;
    help|""|"-h"|"--help") cmd_help ;;
    *) fail "unknown subcommand: $1"; cmd_help; exit 2 ;;
esac
