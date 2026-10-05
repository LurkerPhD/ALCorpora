"""A golden is what a RUN produced, never what the repository shipped.

`golden.source: reference_run` says the comparison target is the project's own
build, run through the same plan, with whatever it wrote kept as the golden.
Pointing `golden.root` at the capture tree (`${TREE}`) changes what that claim
MEANS without changing the field: it says "compare against a file the project
ships", which is a different and much weaker statement — agreement with
another machine's output, produced by another compiler, on another operating
system, in another decade.

Measured on an old corpus: each case directory holds the project's own `.out`,
and they are all reproducible but none is byte-identical to the shipped
reference, because the shipped one carries another machine's banner, another
compiler's wording and a date.  Making those the goldens would have turned a
real comparison into a fixture nobody could satisfy — and, worse, a plan whose
goldens come from the repository is one a broken build can still "pass" if the
copy is fresh enough.

So the two are kept apart STRUCTURALLY: under `reference_run` only a run
output root may hold the golden.  A project's own reference output is an
INPUT, and if comparing against it is wanted, that belongs in the project's
declaration.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

from alcorpora.verification_plan import VerificationPlan  # noqa: E402


def _plan() -> dict:
    """A minimal valid `reference_run` plan, from the shipped template."""
    raw = (REPO / 'skills/onboard-project/reference/'
                  'verification-plan-template.json').read_text(encoding='utf-8')
    return {k: v for k, v in json.loads(raw).items()
            if not k.startswith('_')}


def _with_root(root: str) -> VerificationPlan:
    d = copy.deepcopy(_plan())
    d['golden']['root'] = root
    return VerificationPlan.from_json(d)


def test_the_shipped_template_is_clean():
    """The template must not teach a shape the mechanism refuses."""
    assert _plan()['golden']['source'] == 'reference_run'
    assert VerificationPlan.from_json(_plan()).problems() == []


@pytest.mark.parametrize('root', [
    '${TREE}',
    '${TREE}/verification',
    '${TREE}/verification/Archive/demoout',   # the corpus's own reference set
    '{tree}/anything',
    '/abs/path/into/a/checkout',
    'Archive/demoout',                        # relative: also a literal
])
def test_a_golden_may_not_come_out_of_the_repository(root):
    problems = _with_root(root).problems()
    assert problems, f'{root!r} was accepted as a golden root'
    assert any('golden.root' in w for w in problems), problems


@pytest.mark.parametrize('root', ['{origin}', '{results}'])
def test_a_golden_may_come_from_a_run_output_root(root):
    assert _with_root(root).problems() == []


def test_a_self_verifying_corpus_is_unaffected():
    """`source: none` stores no golden at all, so the rule does not reach it."""
    d = copy.deepcopy(_plan())
    d['golden'] = {'source': 'none', 'root': '{origin}', 'path': '',
                   'stamp': 'DONE'}
    assert VerificationPlan.from_json(d).problems() == []
