---
description: Turn one raw item (title + body) into one or more concept pages, writing SUPERSEDES edges when the new item contradicts the existing wiki.
allowed-tools: memory_search
---

# Instructions

You are processing a single raw item (`title`, `body`, optional `source`,
`url`, `id`) into the wiki's concept graph.

Pipeline:
1. Feed the combined `title\n\nbody` to `cognee.add` (dataset `wiki`,
   node_set `source:<source>`) and `cognee.cognify`.
2. Extract up to 3 top concepts (Cognee triplet completion first, then
   Gemini extraction as fallback).
3. For each concept slug:
   - Read the existing concept page if any.
   - Ask Gemini whether the new item contradicts the page
     (`CONTRADICTION_CHECK` prompt). Cache the verdict in Redis.
   - If `conflict=True`: rewrite the page with Gemini, record the new
     version in Redis (`rewrite_concept`), and write a `SUPERSEDES` edge
     into the Cognee graph (`old_claim → new_claim`, with source + reason).
   - Otherwise: render or refine the page with `CONCEPT_RENDER` and store
     it via `wiki_io.write_concept`.
4. Append one line to `wiki/log.md` summarising touched + self-corrected
   slugs.

Quality bar: every concept page must cite at least one source URL, wikilink
related concepts as `[[slug]]`, and reflect the *latest* claim only — older
claims belong in the SUPERSEDES edge, not the page body.
