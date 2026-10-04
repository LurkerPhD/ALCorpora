"""A seam's fix must reach the compiler, not just the Makefile.

MEASURED DEFECT.  A flag a seam adds to a Makefile never reached a single
file: the objects the failed build had already made were still newer than
their sources, so the next `make` re-linked them and answered `Nothing to be
done`.  The answer was thrown away by a build that never ran, and the seam
was told its fix had failed when the fix had never been applied.

A build system that tracks its own command needs nothing — ninja stores the
command and re-runs it when it changes.  A hand-written Makefile does not, so
its products are cleared first.  The clearing runs AFTER the declared ops,
because for a project whose `clean` is itself broken the patch that fixes it
is one of those ops (measured: a Windows-authored Makefile spells the build
AND the clean with the same broken tool).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / 'skills/fetch-benchmark-project/scripts/fetch.py'


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_tree(tmp_path: Path) -> Path:
    src = tmp_path / 'tree'
    (src / 'source' / 'mis').mkdir(parents=True)
    (src / 'source' / 'mds').mkdir()
    for sub in ('mis', 'mds'):
        (src / 'source' / sub / 'Makefile').write_text('all:\n\t@true\n')
    return src


def test_each_makefile_in_a_make_tree_is_cleaned(tmp_path: Path):
    fetch = _load('fetch_clean_make')
    src = _make_tree(tmp_path)
    ran: list = []
    fetch.run = lambda cmd, cwd: (ran.append(cmd), (True, ''))[1]

    fetch._clean_stale_products(src, 'source', '')
    assert len(ran) == 2, ran
    assert all('clean' in c for c in ran), ran
    assert any(c.endswith('mis clean') for c in ran), ran
    assert any(c.endswith('mds clean') for c in ran), ran


def test_a_build_system_that_tracks_its_own_command_is_left_alone(tmp_path: Path):
    """ninja re-runs a command when it changes; clearing its tree is not ours."""
    fetch = _load('fetch_clean_ninja')
    src = tmp_path / 'tree'
    src.mkdir()
    (src / 'build.ninja').write_text('# a generated build\n')
    (src / 'Makefile').write_text('all:\n\t@true\n')   # even with a Makefile beside it
    ran: list = []
    fetch.run = lambda cmd, cwd: (ran.append(cmd), (True, ''))[1]

    fetch._clean_stale_products(src, '.', '')
    assert ran == [], f'no clean for a ninja tree: {ran}'


def test_the_clean_runs_after_the_ops_that_may_have_fixed_it(tmp_path: Path):
    """The order is the point: a broken `clean` is fixed BY an op."""
    fetch = _load('fetch_clean_order')
    src = _make_tree(tmp_path)
    ran: list = []
    fetch.run = lambda cmd, cwd: (ran.append(cmd), (True, ''))[1]
    fetch._prepare_done = lambda op, s: False

    op = {'op': 'fix-clean', 'run': 'sed -i s/del/rm/ source/mis/Makefile',
          'check': 'true'}
    args = SimpleNamespace(prepare_ops=[op], configure='', build='',
                           test='', tree_subdir='source')
    fetch.native(src, args)

    names = [c for c in ran]
    op_at = next(i for i, c in enumerate(names) if 'sed' in c)
    clean_at = next(i for i, c in enumerate(names) if 'clean' in c)
    assert op_at < clean_at, (
        f'the op must land before the clean it may have fixed: {names}')
