"""
The line at the bottom of the page.

A person says what they want changed — "cut the standup", "make Thursday
optional", "say why the mentor circle matters to ventures" — and the email comes
back changed. It changes in place and keeps one step of undo, so nothing is lost
and there is no diff to read before you can see the result.

It edits one audience's email. It may cut a line, put a cut line back, move a
line between recommended and optional, and rewrite the words on a line. It may
not invent a meeting: the facts come from the calendar, and a line it returns
with an id that was never there is dropped on the floor.

Off unless `COMMS_ANTHROPIC_API_KEY` is set. Nothing renders when it is off — an
input box that does nothing is worse than no box.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings

from . import services

logger = logging.getLogger(__name__)

MAX_TOKENS = 8000

SYSTEM = """You edit one weekly email from a startup accelerator to one of its
audiences: workers, ventures or mentors.

The email says what is coming up in the week ahead and what a person can act on.
Every line is something to do. It never recaps what already happened.

You are given the email as sections of lines, each line with an id, plus the
lines that have been cut from this audience's copy.

Rules:
- Work only with the lines you are given. Never invent a line or an id.
- `in` says whether a line is in this email. Set it false to cut a line, true to
  put a cut line back.
- `rec` is for calendar lines only: true means the audience should be there
  (shown in bold), false means optional.
- You may rewrite `title` and `note`. `note` is one short line saying why this
  audience should turn up, or what to do. Leave it empty rather than filling it
  with something you do not know.
- Plain words. No marketing voice. No "we don't just X, we Y", no "not just...
  but", no em dashes, no empty intensifiers. Do not add enthusiasm nobody wrote.
- Keep the person's own wording wherever the instruction does not ask you to
  change it. Do exactly what was asked and nothing else."""


def available() -> bool:
    return bool(getattr(settings, "COMMS_ANTHROPIC_API_KEY", ""))


def apply(edition, audience: str, send, instruction: str) -> tuple[bool, str]:
    """Apply `instruction` to this audience's email. Returns (changed, problem)."""
    if not available():
        return False, "Asking for edits is not switched on here."
    instruction = (instruction or "").strip()
    if not instruction:
        return False, ""

    import anthropic

    client = anthropic.Anthropic(api_key=settings.COMMS_ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model=settings.COMMS_REWRITE_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Subject: {send.subject}\n\n"
                        f"The email:\n{json.dumps(_current(edition, audience), indent=2)}\n\n"
                        f"Change it like this: {instruction}"
                    ),
                }
            ],
        )
    except Exception as exc:
        logger.warning("comms: edit failed: %s", exc, exc_info=True)
        return False, f"That did not go through: {exc}"

    if response.stop_reason == "refusal":
        return False, "That change was declined."
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        edited = json.loads(text)
    except ValueError:
        logger.warning("comms: edit returned unreadable output")
        return False, "That came back in a shape this page could not read."

    edition.previous = edition.snapshot()
    _apply(edition, audience, edited)
    edition.save(update_fields=["sections", "items", "previous", "updated_at"])

    subject = (edited.get("subject") or "").strip()
    if subject and subject != send.subject:
        send.subject = subject[:200]
        send.save(update_fields=["subject", "updated_at"])
    return True, ""


def _current(edition, audience: str) -> dict:
    return {
        "sections": [
            {
                "k": section["k"],
                "title": section.get("title", ""),
                "calendar": bool(section.get("cal")),
                "lines": [
                    {
                        "id": row["id"],
                        "when": " ".join(p for p in (row.get("day"), row.get("time")) if p),
                        "title": row.get("title", ""),
                        "note": row.get("note", ""),
                        "rec": bool(row.get("rec")),
                    }
                    for row in section["rows"]
                ],
            }
            for section in services.email(edition, audience)
        ],
        "cut": [
            {"id": row["id"], "title": row.get("title", ""), "note": row.get("note", "")}
            for row in services.cut_items(edition, audience)
        ],
    }


def _apply(edition, audience: str, edited: dict) -> None:
    for section in edited.get("sections", []):
        existing = edition.section(section.get("k", ""))
        if existing is not None and section.get("title") is not None:
            existing["title"] = str(section["title"]).strip()

    for line in edited.get("lines", []):
        item = edition.item(line.get("id", ""))
        if item is None or not services.belongs(edition, item, audience):
            continue
        if "title" in line and str(line["title"]).strip():
            item["title"] = str(line["title"]).strip()
        if "note" in line:
            item["note"] = str(line["note"]).strip()
        if "rec" in line:
            item["rec"] = bool(line["rec"])
        off = [a for a in (item.get("off") or []) if a != audience]
        if not line.get("in", True):
            off.append(audience)
        item["off"] = off


_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"k": {"type": "string"}, "title": {"type": "string"}},
                "required": ["k", "title"],
                "additionalProperties": False,
            },
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                    "rec": {"type": "boolean"},
                    "in": {"type": "boolean"},
                },
                "required": ["id", "title", "note", "rec", "in"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["subject", "sections", "lines"],
    "additionalProperties": False,
}
