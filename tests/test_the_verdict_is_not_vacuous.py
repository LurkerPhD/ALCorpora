"""A verdict must not be green when nothing ran.

Measured, on a real project's replay: ctest could not find the executable its
own registration named, so every test was `Not Run` and ctest exited 8 — and
the runner counted that suite as fully passing, because it only looked for
`(Failed)` lines. That is the one outcome a verdict must never produce: a
green that means "nothing ran", not "nothing failed".
"""

from __future__ import annotations

from pathlib import Path

from alcorpora.verification_plan import (Golden, Run, Target,
                                           VerificationPlan)
from alcorpora.verify.suite import Case, _parse_suite


def _plan() -> VerificationPlan:
    return VerificationPlan(
        target=Target(kind='tree'),
        cases={'kind': 'build_system_tests'},
        run=Run(kind='suite', argv=('ctest',), exit_accept=(0,),
                case_timeout_s=60),
        golden=Golden(source='none', root='{origin}', stamp='x'),
        summary={'require_min': {'pass': 1}})


def _cases(ids):
    return [Case(id=i, input=Path(), work=Path()) for i in ids]


def test_a_nonzero_runner_exit_is_crashed():
    """The runner's own exit status is a verdict, whatever the summary says."""
    res = _parse_suite(_plan(), 'whatever', 8, 0.0, _cases(['a', 'b']))
    assert {r.verdict for r in res} == {'crashed'}


def test_a_not_run_test_is_not_a_pass():
    out = ('The following tests FAILED:\n'
           '\t  1 - cjson_add (Not Run)\n'
           'Errors while running CTest\n')
    res = _parse_suite(_plan(), out, 0, 0.0, _cases(['cjson_add', 'other']))
    verdicts = {r.id: r.verdict for r in res}
    assert verdicts['cjson_add'] != 'pass', (
        'a test the runner could not run was counted as passing')
    assert verdicts['other'] == 'pass'


def test_a_skip_is_still_the_runners_own_declaration():
    """`Skipped` is a declaration, not a failure — unlike `Not Run`."""
    out = '\t  1 - a (Skipped)\n'
    res = _parse_suite(_plan(), out, 0, 0.0, _cases(['a']))
    assert res[0].verdict == 'pass'
