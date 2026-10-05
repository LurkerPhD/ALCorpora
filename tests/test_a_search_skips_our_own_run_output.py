"""A project search must not find what our OWN runs left behind.

A run writes into `<tree>/benchmark_results` — staged inputs, substituted
configs, captured output — and that directory sits under the project's root,
so an `rglob` over the project finds it.

Measured while admitting a hand-written make project: a previous run's copy of
the program config sat at `<tree>/benchmark_results/work/D01-01-1A/<config>`
and sorted BEFORE the real `bin/<config>`, so the capture staged the stale
copy — and the substitution it declared was not in it, so the run died naming
a file the project had all along.  The crash landed one run later, in a
different place, with nothing pointing back at the search.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alcorpora.onboard.extract import _our_own_files
from alcorpora.output_layout import RESULTS_DIR


def _tree(root: Path) -> Path:
    (root / 'bin').mkdir(parents=True)
    (root / 'bin' / 'prog.nml').write_text('real config\n')
    stale = root / RESULTS_DIR / 'work' / 'case1'
    stale.mkdir(parents=True)
    (stale / 'prog.nml').write_text('a previous run copied this\n')
    (root / RESULTS_DIR / 'dmap').mkdir(parents=True)
    (root / 'dmap').mkdir()
    return root


def test_the_projects_own_file_wins_over_a_previous_runs_copy(tmp_path: Path):
    root = _tree(tmp_path / 'checkout')
    found = _our_own_files(root.rglob('prog.nml'))
    assert found == [root / 'bin' / 'prog.nml'], (
        'a search over the project returned one of our own run artifacts')


def test_a_run_output_directory_is_not_a_resource(tmp_path: Path):
    root = _tree(tmp_path / 'checkout')
    found = _our_own_files(root.rglob('dmap'), dirs=True)
    assert found == [root / 'dmap']


def test_the_search_is_ordered(tmp_path: Path):
    root = _tree(tmp_path / 'checkout')
    for name in ('b.nml', 'a.nml', 'c.nml'):
        (root / 'bin' / name).write_text(name)
    found = _our_own_files(root.rglob('*.nml'))
    assert [p.name for p in found] == ['a.nml', 'b.nml', 'c.nml', 'prog.nml']
