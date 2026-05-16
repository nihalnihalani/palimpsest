#!/usr/bin/env bash
# wiki-hackathon — the exact command sequence for stage.
# Run interactively; press Enter between steps as you narrate.
set -euo pipefail
cd "$(dirname "$0")/.."

cat <<'PREDEMO'
========================================================================
PRE-DEMO CHECKLIST (run BEFORE judges arrive)

  1. docker compose up -d
  2. source .venv/bin/activate
  3. cp .env.example .env  # if not done; then add GEMINI_API_KEY
  4. wiki reset
  5. wiki seed                 # ~60-90s; pre-bakes the wiki state
  6. ls wiki/concepts/         # confirm 5-8 .md files including a hero target
  7. tar czf snapshot/demo-baked.tar.gz wiki/ .cognee_system/ .data_storage/ 2>/dev/null || \
       tar czf snapshot/demo-baked.tar.gz wiki/   # fall back if Cognee uses different path

  Open in separate windows (THREE visible panes):
    - Window A: Obsidian on the project's wiki/ folder
    - Window B: http://localhost:8001 (RedisInsight) in a browser
    - Window C: a second terminal running:  source .venv/bin/activate && wiki dash
    - Window D (this one): for the demo commands below

  To reset BETWEEN rehearsals:
    wiki reset && tar xzf snapshot/demo-baked.tar.gz
========================================================================
PREDEMO

step() {
    echo
    read -rp "[next: $1] press Enter… "
    eval "$1"
}

# ---- 3-minute stage flow ----

step 'wiki inject-canned contradiction_1'   # the HERO injection
step 'wiki ingest --once'                   # process — page rewrites live in Obsidian
step 'wiki graph supersedes'                # show the new :SUPERSEDES edge w/ provenance
step 'wiki ask "what is the current view on agent memory?"'
step 'wiki eval'                            # held-out 0/3 -> 3/3 metric
step 'wiki lint && cat $(ls -t wiki/reports/*.md | head -1)'

echo
echo "Demo complete."
