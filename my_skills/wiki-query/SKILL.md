---
description: Answer a user question from the wiki using the Cognee knowledge graph plus concept-page snippets, citing every claim.
allowed-tools: memory_search
---

# Instructions

You are answering one user question against the wiki. Follow `query.ask`:

1. Run `cognee.search(GRAPH_COMPLETION)` against the `wiki` dataset to get
   a graph-grounded draft answer.
2. Pull the first 8 concept pages from `wiki/concepts/` and take a 1500-char
   snippet of each — these are your citation pool.
3. Synthesize the final answer with Gemini (`SYNTH_ANSWER` prompt), passing
   the graph completion as `kg`, the concept snippets as `wiki`, and the
   user's question.
4. The answer must:
   - Cite every concept it draws on as `[[slug]]` (slugs from
     `wiki/concepts/<slug>.md`).
   - Prefer the *current* claim — if a concept has a SUPERSEDES history,
     reflect the newest version unless the user asked a time-machine
     question.
   - Say "I don't know from the wiki" rather than invent facts that aren't
     in the graph or the concept pool.

Do not modify the graph here. Self-correction belongs to `wiki-ingest`.
