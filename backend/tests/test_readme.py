"""The README's validation block is the program's real output, not a hand-typed copy."""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from ghosttrack.__main__ import cmd_validate

README = Path(__file__).resolve().parents[2] / "README.md"
OPEN = "<!-- validate:start -->\n```text\n"
CLOSE = "\n```\n<!-- validate:end -->"


def test_readme_validation_block_matches_validate_output():
    text = README.read_text(encoding="utf-8").replace("\r\n", "\n")
    block = text[text.index(OPEN) + len(OPEN):text.index(CLOSE)]
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cmd_validate() == 0
    assert block == buf.getvalue().rstrip("\n"), (
        "README validate block is stale: paste the output of `python -m ghosttrack validate`")
