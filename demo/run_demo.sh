#!/usr/bin/env bash
# Palimpsest -- the exact command sequence for stage.
# Run interactively; press Enter between steps as you narrate.
set -euo pipefail
cd "$(dirname "$0")/.."

cat <<'PREDEMO'
========================================================================
PRE-DEMO CHECKLIST (run BEFORE judges arrive)

  1. ./run.sh all              # readiness check; destructive, do not present after this
  2. ./run.sh prep-demo        # clean seeded state for the live stage flow
  3. source .venv/bin/activate

  Open in separate windows (THREE visible panes):
    - Window A: Obsidian on the project's wiki/ folder
    - Window B: RedisInsight or Redis Cloud console on the Redis database
    - Window C: a second terminal running:  source .venv/bin/activate && wiki dash
    - Window D (this one): for the demo commands below

  To reset BETWEEN rehearsals:
    ./run.sh prep-demo
========================================================================
PREDEMO

step() {
    echo
    read -rp "[next: $1] press Enter… "
    eval "$1"
}

# ---- 3-minute stage flow ----

step 'wiki inject-canned contradiction_1'   # the HERO injection
step 'wiki ingest --once'                   # process -- page rewrites live in Obsidian
step 'wiki graph supersedes'                # show the new :SUPERSEDES edge w/ provenance
step 'wiki ask "what is the current view on agent memory?"'
step 'wiki eval'                            # held-out 0/3 -> 3/3 metric
step 'wiki lint && cat $(ls -t wiki/reports/*.md | head -1)'

echo
echo "Demo complete."
