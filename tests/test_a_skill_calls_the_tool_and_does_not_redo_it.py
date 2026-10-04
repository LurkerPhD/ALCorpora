"""A skill may CALL an operation; it may not REPRODUCE one.

The skills are used by an agent that has to be able to do the same thing
twice and get the same result. Anything deterministic therefore lives in
`bench.py`, and a skill's job is to name the command and say when to run it —
not to describe the steps so the agent can do them by hand.

This is enforced rather than asked, because the failure is quiet: a skill that
reproduces an operation reads perfectly well, and drifts from the tool in some
detail that only shows up when the two disagree. Measured twice — a
re-derived "which directory is the tree" answered wrongly for a checkout with
no build-system marker at the top, and a re-derived "clear the build" deleted
a make project's source directory. Both began as prose in a skill.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SKILLS = sorted((_REPO / 'skills').glob('*/SKILL.md'))


def test_there_is_a_skill_to_check():
    assert _SKILLS, 'no SKILL.md found — this test would pass vacuously'


@pytest.mark.parametrize('skill', _SKILLS, ids=lambda p: p.parent.name)
def test_every_python_invocation_is_bench_py(skill: Path):
    """`python3` in a skill must mean `bench.py`, never a module or a snippet.

    A `python3 -c "from protocolize… import …"` block is the exact shape this
    guards: it looks helpful and it makes the reader responsible for
    reproducing the call correctly, forever.
    """
    text = skill.read_text(encoding='utf-8')
    calls = re.findall(r'python3\s+([^\s\\]+)', text)
    bad = [c for c in calls if Path(c).name != 'bench.py']
    assert not bad, (
        f'{skill.parent.name} invokes python directly: {bad}. '
        f'Every operation is a `bench.py` subcommand — add one if it is '
        f'missing, do not reproduce it here.')


@pytest.mark.parametrize('skill', _SKILLS, ids=lambda p: p.parent.name)
def test_no_inline_python_block(skill: Path):
    """`python3 -c` is a snippet the reader has to keep correct by hand."""
    text = skill.read_text(encoding='utf-8')
    assert 'python3 -c' not in text, (
        f'{skill.parent.name} carries an inline python block; make it a '
        f'`bench.py` subcommand instead')


@pytest.mark.parametrize('skill', _SKILLS, ids=lambda p: p.parent.name)
def test_the_skill_names_the_tool_it_must_use(skill: Path):
    """A skill that never names `bench.py` cannot be telling the agent to use it."""
    text = skill.read_text(encoding='utf-8')
    assert 'bench.py' in text, (
        f'{skill.parent.name} does not mention bench.py at all')
