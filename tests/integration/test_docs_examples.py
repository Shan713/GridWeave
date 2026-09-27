"""Execute every ```python block in the teammate-facing docs so they never go stale."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ["docs/integration_contract.md", "README.md"]


def python_blocks(path: Path) -> list[str]:
    return re.findall(r"```python\n(.*?)```", path.read_text(), flags=re.S)


CASES = [(doc, i, code) for doc in DOCS if (ROOT / doc).exists()
         for i, code in enumerate(python_blocks(ROOT / doc))]


@pytest.mark.parametrize("doc, index, code", CASES, ids=[f"{d}#{i}" for d, i, _ in CASES])
def test_doc_example_runs(doc, index, code, capsys):
    cwd = os.getcwd()
    os.chdir(ROOT)  # examples use repo-relative paths such as data/sample/...
    try:
        exec(compile(code, f"{doc}[block {index}]", "exec"), {"__name__": "__docs__"})
    finally:
        os.chdir(cwd)
