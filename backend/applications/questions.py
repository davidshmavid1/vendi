"""Application questionnaires: a short, fixed list of simple questions.

A question is stored as a plain dict:

    {"id": "products", "type": "short_text", "label": "What do you sell?",
     "required": true, "choices": []}

Types: ``short_text`` (answer up to 200 characters), ``long_text`` (up to
2000), ``single_choice`` (one of ``choices``) and ``acknowledgement`` (a
checkbox; ``true`` when checked, and a required one must be checked).
``id`` is chosen by the organizer and stays the same across edits, so
answers can always be matched to the question they answered.

No conditional questions or file uploads.
"""

import re

from core.exceptions import InvalidRequest

SHORT_TEXT = "short_text"
LONG_TEXT = "long_text"
SINGLE_CHOICE = "single_choice"
ACKNOWLEDGEMENT = "acknowledgement"
QUESTION_TYPES = (SHORT_TEXT, LONG_TEXT, SINGLE_CHOICE, ACKNOWLEDGEMENT)

MAX_QUESTIONS = 20
MAX_LABEL = 300
MIN_CHOICES = 2
MAX_CHOICES = 20
MAX_CHOICE = 100
ANSWER_LIMITS = {SHORT_TEXT: 200, LONG_TEXT: 2000}
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


def normalize_questions(questions: list[dict]) -> list[dict]:
    """Validate an organizer's question list and return it in stored form.

    Raises InvalidRequest (``questions_invalid``) listing every problem."""
    problems: list[dict] = []
    if len(questions) > MAX_QUESTIONS:
        raise InvalidRequest(
            f"At most {MAX_QUESTIONS} questions are allowed.", code="questions_invalid"
        )
    seen: set[str] = set()
    result = []
    for index, question in enumerate(questions):
        qid = question["id"]
        qtype = question["type"]
        label = question["label"].strip()
        choices = [choice.strip() for choice in question.get("choices") or []]

        def problem(message: str, qid=qid, index=index) -> None:
            problems.append({"index": index, "question_id": qid, "message": message})

        if not ID_PATTERN.fullmatch(qid):
            problem("id must be 1-40 lowercase letters, digits, '-' or '_'.")
        elif qid in seen:
            problem("id is used by another question.")
        seen.add(qid)
        if qtype not in QUESTION_TYPES:
            problem(f"type must be one of {', '.join(QUESTION_TYPES)}.")
        if not label or len(label) > MAX_LABEL:
            problem(f"label must be 1-{MAX_LABEL} characters.")
        if qtype == SINGLE_CHOICE:
            if not MIN_CHOICES <= len(choices) <= MAX_CHOICES:
                problem(f"single_choice needs {MIN_CHOICES}-{MAX_CHOICES} choices.")
            elif any(not c or len(c) > MAX_CHOICE for c in choices):
                problem(f"each choice must be 1-{MAX_CHOICE} characters.")
            elif len(set(choices)) != len(choices):
                problem("choices must be different from each other.")
        elif choices:
            problem("only single_choice questions have choices.")
        result.append(
            {
                "id": qid,
                "type": qtype,
                "label": label,
                "required": bool(question.get("required", False)),
                "choices": choices if qtype == SINGLE_CHOICE else [],
            }
        )
    if problems:
        raise InvalidRequest(
            "Some questions are invalid.", code="questions_invalid", details=problems
        )
    return result


def clean_answers(questions: list[dict], answers: dict) -> dict:
    """Check ``answers`` (question id -> value) against ``questions`` and
    return the stored form. Unanswered optional questions are omitted.

    Raises InvalidRequest (``answers_invalid``) listing every problem."""
    problems: list[dict] = []
    known = {q["id"] for q in questions}
    for qid in answers:
        if qid not in known:
            problems.append({"question_id": qid, "message": "Unknown question."})
    cleaned: dict = {}
    for question in questions:
        qid, qtype = question["id"], question["type"]
        value = answers.get(qid)

        def problem(message: str, qid=qid) -> None:
            problems.append({"question_id": qid, "message": message})

        if qtype == ACKNOWLEDGEMENT:
            if value is None or value is False:
                if question["required"]:
                    problem("Please check this box.")
                continue
            if value is not True:
                problem("Must be true or false.")
                continue
            cleaned[qid] = True
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            if question["required"]:
                problem("This question is required.")
            continue
        if not isinstance(value, str):
            problem("Must be text.")
            continue
        value = value.strip()
        if qtype == SINGLE_CHOICE:
            if value not in question["choices"]:
                problem("Choose one of the listed options.")
                continue
        elif len(value) > ANSWER_LIMITS[qtype]:
            problem(f"Must be at most {ANSWER_LIMITS[qtype]} characters.")
            continue
        cleaned[qid] = value
    if problems:
        raise InvalidRequest(
            "Some answers need attention.", code="answers_invalid", details=problems
        )
    return cleaned
