"""Every assertion kind must be able to FAIL. This is criterion (c).

An assertion that cannot fail is not a criterion, it is decoration — and the
corpus already contained one: `005-goldenField` carries a `field_assertion`
whose produced output is byte-identical to its golden, so it compares nothing
and passes because there is nothing to compare.

The test is the same for every kind: build a case that PASSES, then perturb a
DECLARED quantity, and require the verifier to report a difference. If the
verdict does not move, the kind is vacuous.

The perturbation must land on something the plan DECLARED. Perturbing a
derived column and getting "ignored" is correct behaviour, not a vacuous
pass — `field_assertion` exists precisely to skip columns like that. So each
kind below is perturbed where its own declaration says it is looking.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from alcorpora.verify import compare


# ── text_lockstep ─────────────────────────────────────────────

def test_text_lockstep_fails_when_a_number_moves_past_tolerance():
    a = '  1  1.000000E+00  2.000000E+00\n'
    b = '  1  9.000000E+00  2.000000E+00\n'   # 8.0 away: past rtol AND atol
    assert compare.lockstep(a, b, drop=(), rtol=0.05, atol=0.5) != []
    # ... and the same text against itself is NOT a difference, so the test
    # above is measuring the comparison and not some constant complaint.
    assert compare.lockstep(a, a, drop=(), rtol=0.05, atol=0.5) == []
    # A move INSIDE the declared tolerance is not a difference either: the
    # perturbation has to be past what the plan declared, or it is not
    # testing the plan.
    near = '  1  1.200000E+00  2.000000E+00\n'   # 0.2 away, atol 0.5
    assert compare.lockstep(a, near, drop=(), rtol=0.05, atol=0.5) == []


def test_text_lockstep_drops_only_what_was_declared():
    a = 'TIMESTAMP 12345\nVALUE 1.0\n'
    b = 'TIMESTAMP 99999\nVALUE 1.0\n'
    assert compare.lockstep(a, b, drop=(r'^TIMESTAMP',)) == []
    assert compare.lockstep(a, b, drop=()) != []


# ── field_assertion ───────────────────────────────────────────

_MODEL = {
    'tables': [{'titles': ['DISPLACEMENTS'],
                'rows': [{'match': {'first_int': True, 'n_tokens': 3},
                          'fields': {'indices': [1, 2]}}]}],
}
_GEN = '   DISPLACEMENTS\n    1  1.0  2.0\n'
_BASE = '   DISPLACEMENTS\n    1  1.0  2.0\n'


def test_field_assertion_moves_when_a_declared_column_moves():
    moved = _GEN.replace('1.0', '9.0')
    assert compare.field_lockstep(moved, _BASE, model=_MODEL) != []
    assert compare.field_lockstep(_GEN, _BASE, model=_MODEL) == []


def test_field_assertion_rightly_ignores_a_column_it_did_not_declare():
    """The other half: not every difference is a failure.

    A model that declared the wrong columns would fail here, which is what
    makes the test above about the DECLARATION and not about the comparator's
    eagerness.
    """
    wider = {'tables': [{'titles': ['DISPLACEMENTS'],
                         'rows': [{'match': {'first_int': True},
                                   'fields': {'indices': [1, 2]}}]}]}
    assert compare.field_lockstep(_GEN, _BASE, model=wider) == []


# ── artifact ──────────────────────────────────────────────────

def test_an_artifact_assertion_moves_when_the_file_is_gone(tmp_path: Path):
    """Checks the verifier end to end, not just the comparator.

    The three kinds above are compared directly; this one goes through the
    runner, because "the file is missing" is not a text comparison and the
    failure mode it guards is a plan that reads a path nobody wrote.
    """
    from alcorpora.verification_plan import VerificationPlan
    from alcorpora.verify import suite
    from alcorpora.verify.cli import _plan_and_env

    root = tmp_path / 'p'
    (root / 'build').mkdir(parents=True)
    (root / 'v' / 'c1').mkdir(parents=True)
    (root / 'v' / 'c1' / 'c1.in').write_text('deck\n')
    (root / 'build' / 'protocol.json').write_text(
        json.dumps({'protocol_version': 1, 'compile_commands': []}))
    plan = {
        'plan_version': 1, 'target': {'kind': 'tree', 'dirs': ['{tree}']},
        'protocol': 'protocol.json',
        'cases': {'kind': 'one_per_dir', 'root': '${TREE}/v', 'exclude': [],
                  'id': {'from': 'dirname'},
                  'input': {'name': '{id}.in', 'id_spelling': {}},
                  'skip_without_input': False},
        'inputs': {'from': 'case_dir', 'exclude_suffix': []},
        'staging': [],
        'run': {'kind': 'process', 'argv': ['/bin/true'], 'cwd': '{work}',
                'exit_accept': [0], 'case_timeout_s': 10},
        'produced': [{'name': '{work}/c1.out', 'as': 'out'}],
        'golden': {'source': 'none', 'stamp': 'x'},
        'assertions': [{'kind': 'artifact', 'path': '{out}'}],
        'chains': {}, 'report': {'repeats': 1},
        'summary': {'require_zero': ['diff'], 'require_min': {'pass': 1}},
        'provenance': '',
    }
    (root / 'build' / 'verification_plan.json').write_text(json.dumps(plan))

    p, env = _plan_and_env(root / 'build', None, None, None, root)
    result = suite.run(p, env)
    assert not result.ok, (
        'a plan asserting an artifact the run never wrote must not pass')
    assert result.counts.diff == 1


def test_a_weaker_declaration_is_visible_to_criterion_b():
    """The acceptance run's criterion (b), as a test.

    Measured while re-deriving a corpus's declaration: the re-derived set
    dropped TWO of the three related counters its own notes record as
    varying between runs of the same binary. It passed 132/132 that day —
    because those two runs happened to agree on the third — so criterion (a)
    saw nothing wrong. Comparing the RULES (which lines each set judges)
    rather than the verdict is what exposed it.
    """
    import re

    def judged(patterns, text):
        ps = [re.compile(p) for p in patterns]
        return frozenset(i for i, ln in enumerate(text.splitlines())
                         if any(p.search(ln) for p in ps))

    report = (
        "   DATE: 10/ 3/26\n"
        "0*** USER INFORMATION MESSAGE 3028  B =    21 BBAR = 100\n"
        "      C =    20 CBAR = 100\n"
        "      R =    23\n"
        "      MAXBLK = 5\n"
    )

    original = [r'^\s*DATE:', r'B\s+=\s+\d+\s+BBAR',
                r'^\s+C\s+=\s+\d+\s+CBAR', r'^\s+R\s+=\s+\d+']
    weaker = [r'^\s*DATE:', r'^\s+C\s+=\s+\d+\s+CBAR',
              r'^\s+R\s+=\s+\d+']

    assert judged(original, report) != judged(weaker, report), (
        'the two declarations judge different lines — that IS the difference '
        'a verdict-only check cannot see')
    assert 1 in (judged(original, report) - judged(weaker, report)), (
        'the dropped line is the B counter, which the corpus records as '
        'varying between runs')
