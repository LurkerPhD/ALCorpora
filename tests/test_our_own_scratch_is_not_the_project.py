"""Our own scratch must be invisible to the search that reads the project.

Both defects here were surfaced by making a minimal sample re-provable.  The
samples keep their `CMakeLists.txt` at the benchmark directory's TOP, so their
source root IS the benchmark directory — and that is what puts `.work/` inside
the tree being mirrored and searched:

* `_mirror` copies the tree to `${tree}/.work/tree`, so the destination is
  INSIDE the source.  Copying then copies the copy, which contains the copy:
  measured, `shutil.copytree` recursed until the interpreter's stack ran out
  (the traceback surfaced inside `fnmatch.filter`).  The subtree that contains
  the destination is the only one that can do this.
* `.work/tree` is a mirror of the WHOLE project, so an unfiltered case search
  finds the project's declaration twice.  `_locate` then refuses — correctly,
  by its own rule — as ambiguous, over a file that was ours, not the
  project's.
"""

from __future__ import annotations

from pathlib import Path

from alcorpora.onboard.extract import _find_case_dirs, _find_case_lists
from alcorpora.onboard.selfcheck import _mirror


def test_the_mirror_does_not_recurse_into_itself(tmp_path: Path):
    """`dest` inside `source` must terminate, and must not copy `.work`."""
    tree = tmp_path / 'tree'
    (tree / 'src').mkdir(parents=True)
    (tree / 'CMakeLists.txt').write_text('project(x)\n')
    (tree / 'src' / 'a.c').write_text('int main(void){return 0;}\n')

    dest = tree / '.work' / 'tree'
    dest.parent.mkdir(parents=True)
    _mirror(tree, dest)

    assert (dest / 'CMakeLists.txt').is_file()
    assert (dest / 'src' / 'a.c').is_file()
    assert not (dest / '.work').exists(), (
        'the mirror must not carry our own scratch — that is the recursion')


def test_the_case_search_skips_our_own_scratch(tmp_path: Path):
    """The mirrored copy must not read as a second declaration."""
    tree = tmp_path / 'proj'
    (tree / 'verification' / 'c1').mkdir(parents=True)
    (tree / 'verification' / 'c1' / 'c1.in').write_text('deck\n')
    # What a previous capture of THIS project left: a mirror under `.work`.
    (tree / '.work' / 'tree' / 'verification' / 'c1').mkdir(parents=True)
    (tree / '.work' / 'tree' / 'verification' / 'c1' / 'c1.in').write_text('deck\n')

    found = _find_case_dirs(tree)
    assert found == [tree / 'verification'], found


def test_the_case_list_search_skips_our_own_scratch(tmp_path: Path):
    tree = tmp_path / 'proj'
    tree.mkdir()
    (tree / 'cases.txt').write_text('c1.in\n')
    (tree / '.work' / 'tree').mkdir(parents=True)
    (tree / '.work' / 'tree' / 'cases.txt').write_text('c1.in\n')

    assert _find_case_lists(tree) == [tree / 'cases.txt']
