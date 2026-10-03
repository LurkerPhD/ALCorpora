"""The plan declares WHERE the thing under test is; the mechanism resolves it.

The same plan runs against two builds that put their artifacts in different
places: the project's own build, and the generated flat build (§B self-verify
runs it on the reference, then on the flat build).  So the plan declares
candidate directories and the mechanism resolves by name — taking the one
that is there, refusing to guess when several are.
"""

from __future__ import annotations

import pytest

from protocolize.verification_plan import (Golden, Run, Target,
                                           VerificationPlan)
from protocolize.verify import reference
from protocolize.verify.suite import PlanRunError


def _plan(kind: str, product: str = '', dirs=()) -> VerificationPlan:
    return VerificationPlan(
        target=Target(kind=kind, product=product, dirs=tuple(dirs)),
        cases={'kind': 'build_system_tests'},
        run=Run(kind='suite', argv=('ctest',), case_timeout_s=60),
        golden=Golden(source='none', root='{origin}', stamp='ok'),
        summary={'require_min': {'pass': 1}})


def test_a_product_resolves_to_the_candidate_that_exists(tmp_path):
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'demo').write_text('x')
    plan = _plan('product', 'demo', ('{tree}/bin', '{tree}/curr/bin'))
    assert reference.resolve_product(plan, tmp_path) == tmp_path / 'bin' / 'demo'


def test_a_product_resolves_in_the_second_candidate_too(tmp_path):
    """The project's own layout is a candidate like any other — the ordering
    is a preference, not an assumption."""
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out' / 'demo').write_text('x')
    plan = _plan('product', 'demo', ('{tree}/bin', '{tree}/out'))
    assert reference.resolve_product(plan, tmp_path) == tmp_path / 'out' / 'demo'


def test_two_candidates_holding_the_product_are_refused(tmp_path):
    for d in ('bin', 'curr/bin'):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / 'demo').write_text('x')
    plan = _plan('product', 'demo', ('{tree}/bin', '{tree}/curr/bin'))
    with pytest.raises(PlanRunError, match='more than one'):
        reference.resolve_product(plan, tmp_path)


def test_a_tree_resolves_to_where_the_registrations_are(tmp_path):
    """The build system's registry is the marker, wherever it sits."""
    (tmp_path / 'build').mkdir()
    (tmp_path / 'build' / 'CTestTestfile.cmake').write_text('x')
    plan = _plan('tree', '', ('{tree}/build', '{tree}'))
    assert reference.resolve_tree(plan, tmp_path) == tmp_path / 'build'


def test_a_tree_built_in_place_resolves_to_the_root(tmp_path):
    (tmp_path / 'CTestTestfile.cmake').write_text('x')
    plan = _plan('tree', '', ('{tree}/build', '{tree}'))
    assert reference.resolve_tree(plan, tmp_path) == tmp_path


def test_a_product_plan_without_candidates_still_resolves(tmp_path):
    """`dirs` is optional: a plan captured before it falls back to the
    conventional places rather than failing."""
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'demo').write_text('x')
    plan = _plan('product', 'demo')
    assert reference.resolve_product(plan, tmp_path) == tmp_path / 'bin' / 'demo'


def test_dirs_must_be_bindings_not_literal_paths(tmp_path):
    plan = _plan('product', 'demo', ('/usr/local/bin',))
    assert any('target.dirs[0]' in p for p in plan.problems())
