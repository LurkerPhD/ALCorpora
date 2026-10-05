"""A `self_report` assertion must REFUSE when it matches nothing.

A suite corpus's verdict partly comes from the corpus's OWN summary table —
LAPACK prints one with a `numerr`/`otherr` column per precision.  The
assertion reads that table out of the suite's stdout, which makes it exactly
the kind of check that can pass VACUOUSLY: if the pattern matches nothing,
there are no non-zero values to find, and a naive implementation reports "no
problems".

Measured against the real mechanism, feeding it four stdouts:

    table present, numerr=0            -> that assertion passes
    table present, numerr=7            -> refused ("numerr is 7")
    stdout empty                       -> refused ("matched nothing")
    stdout without the table           -> refused ("matched nothing")

So the assertion has teeth, and a passing run therefore PROVES the table was
found — which is what makes the two `require_min` floors load-bearing rather
than decorative.
"""

from __future__ import annotations

from alcorpora.verification_plan import VerificationPlan
from alcorpora.verify.suite import check_self_reports

#: The corpus's own shape: a heading the section_pattern matches, and the
#: total row the assertion reads.
_TABLE = """
 ----------------- LAPACK: Default API and Extended API (_64) -----------------
  SUMMARY                nb test run      numerical error          other error
  ========================================================================
  REAL                       1632600          0   (0.00%)          0   (0.00%)
  --> ALL PRECISIONS         5451263          {num}   (0.00%)          0   (0.00%)
"""


def _plan() -> VerificationPlan:
    return VerificationPlan.from_json({
        'plan_version': 1,
        'target': {'kind': 'tree', 'dirs': ['{tree}']},
        'protocol': 'protocol.json',
        'cases': {'kind': 'build_system_tests', 'tests': []},
        'run': {'kind': 'suite', 'argv': ['ctest'], 'cwd': '{target}',
                'exit_accept': [0], 'case_timeout_s': 60},
        'golden': {'source': 'none', 'root': '{origin}', 'path': '',
                   'stamp': 'DONE'},
        'assertions': [{
            'kind': 'self_report', 'in': 'suite_stdout',
            'section_pattern': (
                r'^-{5,}\s*([A-Za-z][A-Za-z0-9 :_,()/.]*? and '
                r'[A-Za-z][A-Za-z0-9 :_,()/.]*?)\s*-{5,}$'),
            'total_label': 'ALL PRECISIONS',
            'require_min': {'run': 1},
            'require_zero': ['numerr', 'otherr'],
        }],
        'summary': {'require_zero': ['crash'], 'require_min': {'pass': 1}},
        'provenance': '',
    })


def test_a_clean_table_passes():
    problems, evidence = check_self_reports(
        _plan(), _TABLE.replace('{num}', '0'))
    assert problems == []
    assert evidence, 'a pass must state the numbers it read'


def test_a_nonzero_column_is_refused():
    problems, _ = check_self_reports(_plan(), _TABLE.replace('{num}', '7'))
    assert problems, 'a numerical error in the corpus\'s own table went '
    assert any('numerr' in p for p in problems), problems


def test_a_missing_table_is_refused_not_passed():
    """The vacuous case: no table means no non-zero values to find."""
    for stdout in ('', 'unrelated output\nno summary here\n'):
        problems, _ = check_self_reports(_plan(), stdout)
        assert problems, (
            'an assertion that matched NOTHING reported success — the '
            'verdict would have no checks behind it')
        assert any('matched nothing' in p for p in problems), problems


def test_the_evidence_reaches_the_runs_output():
    """A green verdict must show the numbers behind it.

    A run that says only "no complaint" is indistinguishable from one that
    read nothing — and this is the kind whose whole verdict comes from a
    number the PROJECT printed about itself.
    """
    from alcorpora.verify import suite

    plan = _plan()
    stdout = _TABLE.replace('{num}', '0')

    class _Env:
        jobs = 1

    # A run with no results cannot be green (`require_min`), so give it the
    # one pass the plan asks for.
    ran = [suite.CaseResult('c1', 'pass', 0.0)]
    result = suite._finish(plan, ran, _Env(), suite_stdout=stdout)
    assert result.ok, result.output
    assert 'read:' in result.output, result.output
    assert 'numerr=0' in result.output, result.output
