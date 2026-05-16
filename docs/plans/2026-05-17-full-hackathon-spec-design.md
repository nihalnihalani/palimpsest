# Full Hackathon-Spec Migration — Design

Date: 2026-05-17
Branch: `feat/full-hackathon-spec`
Status: design approved, executing

## Why

The repo was built against `cognee==0.5.8` (`add` + `cognify` + `search` API). The Cognee × Redis hackathon brief (`hackathon-resources/cognee-hackathons/cognee-redis-hackathon-2026-05-16/README.md`) targets cognee ≥ 1.0's `remember` / `recall` / `forget` / `improve` API and explicitly requires a `SkillRunEntry` + `improve_skill` propose-then-apply loop. To match the brief literally — and to maximise the "Best Use of Redis" surface — we upgrade.

Devil's advocate review (see commit history) flagged two showstoppers in the *pre-upgrade* state:
1. `.env.example` advertised `VECTOR_DB_PROVIDER=redis`, but cognee 0.5.8 ships **no** Redis vector adapter (`cognee/infrastructure/databases/vector/supported_databases.py = {}`). The project was silently using LanceDB.
2. The "10-min-TTL Gemini contradiction cache in `gemini_io.py`" claim was wrong — it lives in `redis_bus.py:115-121` and is an exact-match cache, not semantic.

Both are addressed below.

## Scope

### MUST do
- `F0` — Fix `.env.example` (remove the bogus vector-provider claim).
- `P1` — Upgrade cognee to 1.x with the `[redis]` extra; add `redisvl≥0.18.2`.
- `P2` — Port `cognee_io.py` to the new `remember`/`recall` API. Keep public function names stable so the rest of the codebase (ingest.py, query.py, eval.py, rethink.py, lint.py, timemachine.py) stays untouched.
- `P3` — Real self-improvement loop:
  - `my_skills/{wiki-ingest, wiki-query, code-review}/SKILL.md` per brief frontmatter.
  - New `src/palimpsest/skill_loop.py` implementing the brief's `remember(SkillRunEntry, …, skill_improvement={apply: False})` then `improve_skill(…, apply=True)` cycle.
  - New `wiki improve` CLI subcommand.
- `P4` — RedisVL `SemanticCache` for query→answer (NOT the contradiction cache, which is correctly exact-match):
  - `src/palimpsest/gemini_vectorizer.py` — custom `BaseVectorizer` reusing Gemini embeddings (avoids the torch dep that `HFTextVectorizer` would pull in).
  - `src/palimpsest/answer_cache.py` — `SemanticCache` wrapper.
  - Wire into `query.ask()` ahead of the existing verdict cache.
- `P5` — `wiki vector-smoke` CLI — asserts the resolved vector provider, dumps `docs/evidence/vector_provider.txt`. This is the DA's "don't lie about Redis vectors" check.
- `P6` — `SUBMISSION.md` at repo root, filled in *truthfully*:
  - Redis = session memory (Streams + JSON + Pub/Sub) + RedisVL SemanticCache + cognee `session_id=` routing.
  - Cognee permanent graph (LanceDB-backed by default; Kuzu graph engine).
  - Before/after evidence: `wiki eval` run N=5, median captured.
- `P7` — Before/after evidence in `docs/evidence/`.

### Skipped (per DA)
- Swap to `cognee-community-vector-adapter-redis` v0.2.0 — even on cognee 1.1.0 it's a separate add and currently we have no node/payload metadata to take advantage of its filter API. Out of scope for 3-min demo value.
- Replacing `chat_session.py` with `SemanticMessageHistory` — regresses recent `wiki chat list/resume/delete`. Instead: add a *sidecar* `SemanticMessageHistory` for retrieval recall inside `chat._send_to_gemini`, only if Tier-1 finishes with time to spare.
- `EmbeddingsCache` — Gemini embedding cost is trivial at our volume.
- Copying `agent-skills/` into `.claude/skills/` — irrelevant to judging; agent-skills is for local Claude Code, not what `cognee.remember(content_type="skills")` ingests.

## Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Cognee 1.x has different env var names → entire app breaks on `wiki doctor` | Read cognee 1.x `.env.template` after install. Update `.env.example`. Run `wiki doctor` as the first verification gate. |
| New API renamed `cognee.prune.prune_data` etc. → `wiki reset` breaks | Wrap reset in a 1.x-compatible call (`forget`?). Test before commit. |
| `wiki rethink` uses `cognee.memify` which may not exist in 1.x | Inspect; if removed, point `wiki rethink` at `improve_skill`. |
| RedisVL `SemanticCache` pulls torch by default | Custom `GeminiTextVectorizer` extending `BaseVectorizer` — no HF. |
| Live `wiki eval` is stochastic | Run N=5, capture median + raw outputs, persist all 5 runs to `docs/evidence/`. |
| Migration breaks existing tests | Run `pytest -x` after each major file change. Branch is disposable. |

## Team & execution order

| Role | Owner | Phase |
| --- | --- | --- |
| brief-scout | (done) research | P0 |
| redisvl-scout | (done) research | P0 |
| skills-scout | (done) research | P0 |
| adaptor-scout | (done) research | P0 |
| devils-advocate | (done) review | P0 |
| upgrader | main session | P1 |
| api-porter | main session | P2 |
| skill-loop builder | main session | P3 |
| cache builder | parallel agent | P4 (after P1) |
| smoke + .env writer | parallel agent | P5 / F0 (after P1) |
| submission writer | parallel agent | P6 (depends on P3 done) |
| evidence runner | main session | P7 (final) |
| verifier | main session | P8 (final) |

Parallel phase happens after P1 lands. Sequential phases are P2 → P3.

## Verification gates

1. `pip install` returns 0 and `python -c "import cognee; print(cognee.__version__)"` ≥ 1.0
2. `python -c "from cognee import remember, recall"` succeeds
3. `wiki doctor` exits 0
4. `wiki reset && wiki seed && wiki load-baseline && wiki ask "<query>"` end-to-end works
5. `wiki vector-smoke` reports a non-null provider
6. `wiki improve` proposes a skill rewrite for a deliberately-low-score run
7. `wiki eval` N=5 median ≥ baseline-median (i.e., we didn't regress quality)
8. `pytest -q` passes
