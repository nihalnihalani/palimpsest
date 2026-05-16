#!/usr/bin/env bash
# wiki-hackathon orchestrator. One script to rule them all.
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
    if ! have_venv; then fail ".venv not found — run: ./run.sh setup"; exit 1; fi
    set +u
    # shellcheck disable=SC1091
    source .venv/bin/activate
    set -u
}
require_env_key() {
    if ! [ -f .env ]; then fail "missing .env (run: ./run.sh setup)"; exit 1; fi
    if ! grep -q '^GEMINI_API_KEY=..*' .env; then
        fail "GEMINI_API_KEY is empty in .env — edit it and re-run"
        exit 1
    fi
}

# --- subcommands ---
cmd_setup() {
    step "setup"
    require_cmd docker
    require_cmd python3.11

    if docker compose ps --status running 2>/dev/null | grep -q wiki-redis; then
        ok "redis already up"
    else
        info "starting docker compose..."
        docker compose up -d
        # wait up to 10s for PING
        for i in $(seq 1 10); do
            if docker exec wiki-redis redis-cli PING 2>/dev/null | grep -q PONG; then
                ok "redis PONG"
                break
            fi
            sleep 1
        done
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
    wiki doctor || true   # informational, never fail the seed step
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

cmd_all() {
    cmd_setup
    require_env_key
    cmd_doctor || { fail "doctor failed — fix before continuing"; exit 1; }
    cmd_verify
    cmd_seed
    cmd_demo
}

cmd_help() {
    cat <<HELP
wiki-hackathon orchestrator

Usage: ./run.sh <subcommand>

  setup    Idempotent: docker up + venv + pip install + .env scaffold
  doctor   Diagnostic dump (Redis health, Cognee graph stats, env, logs)
  verify   Run scripts/verify_live.sh (9-step live smoke)
  seed     wiki reset && wiki seed && wiki load-baseline
  demo     Run demo/run_demo.sh (3-min stage flow)
  rethink  wiki rethink (cognee.memify graph enrichment)
  test     pytest -q tests/
  all      setup → doctor → verify → seed → demo
  help     This message
HELP
}

# --- main ---
case "${1:-help}" in
    setup)    cmd_setup ;;
    doctor)   cmd_doctor ;;
    verify)   cmd_verify ;;
    seed)     cmd_seed ;;
    demo)     cmd_demo ;;
    rethink)  cmd_rethink ;;
    test)     cmd_test ;;
    all)      cmd_all ;;
    help|""|"-h"|"--help") cmd_help ;;
    *) fail "unknown subcommand: $1"; cmd_help; exit 2 ;;
esac
