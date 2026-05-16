"""All Gemini prompt templates. Strict JSON outputs where parsing is needed."""
from __future__ import annotations

CONCEPT_RENDER = """\
You are maintaining a wiki on the topic "AI agents in 2026". The current concept
is "{title}".

You will be given (a) the existing concept page content if any, and (b) a new
source item that mentions this concept. Produce an updated concept page in
Obsidian-flavored Markdown.

Rules:
- 2-4 short paragraphs.
- Use [[wikilinks]] for related concepts when natural.
- Do NOT invent facts that aren't in the existing page or the new item.
- End with a "## Sources" section listing the new and any prior sources as
  bullets with URLs when available.

Existing page (or "(empty)"):
{existing}

New item:
Source: {source}
Title: {item_title}
Body: {item_body}
URL: {item_url}

Return ONLY the markdown, no preamble.
"""

CONTRADICTION_CHECK = """\
You compare a wiki concept page against new evidence.

Concept slug: {slug}
Current page:
\"\"\"{page}\"\"\"

Incoming item:
\"\"\"{item}\"\"\"

Return STRICT JSON, no prose, with these keys:
  "conflict": boolean — true ONLY if the item directly contradicts a substantive
              claim on the page (not merely adds or refines).
  "evidence": string — one sentence quoting the contradicting fact pair.
  "old_claim": string — the page's claim being superseded.
  "new_claim": string — the item's claim that supersedes it.
  "rewrite":  string — the FULL replacement markdown for the page if conflict
              is true; empty string otherwise.

Example: {{"conflict": true, "evidence": "page says X happened Jan 15, item says Jan 22",
"old_claim": "X happened Jan 15", "new_claim": "X happened Jan 22",
"rewrite": "---\\ntitle: ...\\n---\\n..."}}
"""

SYNTH_ANSWER = """\
Answer the user's question using ONLY the supplied KG context and wiki snippets.
Be concise (3-5 sentences). Cite concept page slugs in [[brackets]] when used.
If the answer is unknown, say so.

KG context:
{kg}

Wiki snippets:
{wiki}

Question: {question}
"""

EVAL_GRADER = """\
Grade the model's answer against the rubric. Return STRICT JSON:
  "score": integer 0-3 — how many required entities are correctly named
  "found": list of strings — the entities found in the answer
  "missing": list of strings — required entities not in the answer

Required entities: {required}

Model answer:
\"\"\"{answer}\"\"\"
"""

EXTRACT_CONCEPTS = """\
Extract 1-3 distinct concept names from this item that are worth a dedicated wiki page on the topic "AI agents in 2026". Return STRICT JSON: {{"concepts": ["concept1", "concept2"]}}. Use short noun phrases (1-4 words each). Prefer entities, technologies, or topics over verbs.

Item title: {title}
Item body: {body}
"""

RETHINK = """\
You inspect a slice of a knowledge graph and propose improvements.

Entity: {entity}
Current relationships (subject -[rel]-> object):
{neighborhood}

Return STRICT JSON:
  "contradictions": [{{"a": "claim1", "b": "claim2", "explanation": "..."}}, ...]
  "inferred_edges": [{{"from": "<entity1>", "to": "<entity2>", "rel": "RELATED_TO", "reason": "..."}}, ...]

Only propose inferred edges that are well-supported by the existing relationships.
If nothing to add, return empty arrays.
"""
