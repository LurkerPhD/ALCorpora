"""The plan must be verified by the directory it was WRITTEN to.

Onboarding's exit gate runs the freshly captured plan against the reference
and refuses the plan unless the reference passes it.  That run hands the plan
to ``bench_cli``, which loads ``<dir>/verification_plan.json`` and discovers
the tree to run under it — so the directory it is given decides whether the
plan is found at all.

It was given the raw build tree (CMake's own ``build/`` subdirectory), while
the plan is written one level up at the tree ROOT.  ``bench_cli`` then looked
for a plan that is not there and reported ``no verification_plan.json``, so a
correct plan was refused by its own gate — a plan that never ran is not a plan
that passed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ONBOARD_PY = (Path(__file__).resolve().parents[1] / 'scripts' / 'onboard.py')


def _load_onboard():
    spec = importlib.util.spec_from_file_location('_onboard_under_test',
                                                  ONBOARD_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_plan_is_verified_in_the_directory_it_was_written(tmp_path,
                                                              monkeypatch):
    mod = _load_onboard()
    from protocolize.edd import bench_cli

    tree = tmp_path / 'origin_release'
    (tree / 'build').mkdir(parents=True)          # CMake's own subdirectory
    plan_path = tree / 'verification_plan.json'    # written at the ROOT
    plan_path.write_text('{}')

    seen: list[list[str]] = []
    monkeypatch.setattr(bench_cli, 'main',
                        lambda argv: (seen.append(list(argv)), 0)[1])

    rc = mod._run_plan_against_reference(plan_path)

    assert rc == 0
    assert seen and seen[0] == [str(tree)], (
        f'bench_cli must be pointed at the plan\'s own directory {tree}, '
        f'not the build subdirectory; got {seen}')
