"""Path setup for the onboard-corpus skill's tests.

These tests exercise the skill's own implementation — ``protocolize/onboard/``,
the mechanical half the skill's ``scripts/onboard.py`` wraps — and not the
transpiler's ``src/``.  They live beside the skill rather than under
``tests/`` because ``tests/`` is self-contained: what it tests is the
transpiler, and a skill's implementation is not part of that.

They are still UNIT tests — pure Python, no compiler configs — so the gate
discovers and runs them exactly as it runs ``tests/unit/`` (see
``protocolize/edd/runner.py``: ``skill_test_dirs`` / ``unit_test_file``).

The repository root goes on ``sys.path`` so ``protocolize.*`` and ``src.*``
import the same way they do from ``tests/unit/conftest.py``.  The index is 4
because this file sits four levels below the root
(``.github/skills/onboard-corpus/tests/``).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
