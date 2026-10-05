"""A verification that COMPARES output is measured BEFORE the verdict.

MEASURED DEFECT, found in a regression: a comparison whose volatile lines
happened to match — two reference runs landing inside the same second, which
132 short decks manage easily — passed with `drop: []`, no rules at all.  So
the failure that would have provoked the measurement never came, the plan
kept no rules, and the NEXT minute's run failed all 132 cases.  A `PASS` that
depends on the clock is not a verdict.

So the measurement runs wherever the golden is a `reference_run`: the lines
that moved between two runs of one binary ARE the volatile lines, and that is
deterministic.  The seam keeps only what a measurement cannot reach.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from alcorpora.verification_plan import VerificationPlan
from alcorpora.verification_plan import Golden, Run, Target

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / 'skills/fetch-benchmark-project/scripts/fetch.py'


def _load():
    spec = importlib.util.spec_from_file_location('fetch_measured', _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['fetch_measured'] = mod
    spec.loader.exec_module(mod)
    return mod


def _plan(**assertions):
    return VerificationPlan(
        target=Target(kind='tree'),
        cases={'kind': 'one_per_dir'},
        run=Run(kind='process', argv=('x',), exit_accept=(0,),
                case_timeout_s=60),
        golden=Golden(source='reference_run', root='{origin}', stamp='x'),
        assertions=tuple(assertions.values()))


def test_a_measured_rule_reaches_the_comparison_assertion():
    fetch = _load()
    plan = _plan(lock=dict(kind='text_lockstep', drop=['^DATE:']))
    fetch._with_drops(plan, [r'^\s*END\s+TIME:.*$'])
    drop = plan.assertions[0]['drop']
    assert '^DATE:' in drop, 'the declared rule is kept'
    assert r'^\s*END\s+TIME:.*$' in drop, 'and the measured one is added'


def test_a_rule_is_not_added_to_a_non_comparison_assertion():
    """An `artifact` assertion compares no text; a drop rule there is inert."""
    fetch = _load()
    plan = _plan(art=dict(kind='artifact', path='p', exists=True))
    fetch._with_drops(plan, [r'^x$'])
    assert 'drop' not in plan.assertions[0], (
        'a rule added where nothing reads it would look like a declaration '
        'and change nothing')


def test_every_comparison_assertion_gets_the_rule():
    fetch = _load()
    plan = _plan(a=dict(kind='text_lockstep'), b=dict(kind='field_assertion'))
    fetch._with_drops(plan, [r'^x$'])
    assert all(a['drop'] == [r'^x$'] for a in plan.assertions)
