"""Held-out eval: a question that flips 0/3 -> 3/3 after ingest.

This is the metric we put on screen during the demo to prove self-improvement
is real, not theater.
"""
from __future__ import annotations

from . import gemini_io, query
from .prompts import EVAL_GRADER

EVAL_QUESTION = (
    "Who is leading agent memory research in 2026? "
    "Name specific people, projects, or companies."
)
EVAL_REQUIRED = ["Karpathy", "MemGPT", "Cognee"]


def run() -> dict:
    answer = query.ask(EVAL_QUESTION)
    grade = gemini_io.generate_json(EVAL_GRADER.format(
        required=EVAL_REQUIRED, answer=answer,
    ))
    score = int(grade.get("score", 0))
    return {
        "question": EVAL_QUESTION,
        "answer": answer,
        "score": score,
        "max": len(EVAL_REQUIRED),
        "found": grade.get("found", []),
        "missing": grade.get("missing", []),
    }
