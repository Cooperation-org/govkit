"""docs/template-decisions.md: not one comment, in any template.

None of these four are stripped on the way out — a browser is handed every one
of them, so anyone who views source reads our notes to ourselves.
"""

import re
from pathlib import Path

COMMENT = re.compile(r"^\s*(\{#|<!--|/\*|//)")
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def test_no_template_carries_a_comment():
    offenders = [
        f"{path.relative_to(TEMPLATES)}:{number}: {line.strip()[:60]}"
        for path in sorted(TEMPLATES.glob("**/*.html"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if COMMENT.match(line)
    ]
    assert offenders == [], "move the reasoning to docs/template-decisions.md"
