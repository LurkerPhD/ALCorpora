"""A re-prove that fails must not erase an established result.

The row for a project in `benchmark.csv` records a proof that was CARRIED OUT.
A `--readmit` that fails establishes nothing — and its failure is often about
the invocation rather than the project, since `--configure`/`--build`/`--test`
all default to CMake.

Measured: an operator's re-prove of a make project with the default CMake
flags failed the native step, and the tool overwrote the project's `PASS` row
with `REFUSED` — every other column blanked.  The CSV then reported a REGRESSED
project when the truth was a misconfigured command, and nothing recorded which
of the two had happened.

So a failed re-prove reports and leaves the row alone.  That keeps the CSV
meaning "the last state that was actually proved", which is how its rows are
produced in the first place.
"""

from __future__ import annotations

import csv
import importlib.util
from argparse import Namespace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_SCRIPT = REPO / 'skills/fetch-benchmark-project/scripts/fetch.py'

_ROW = ('number,name,language,build_system,verification,native,build_protocol_a,'
        'verify_on_project_b,verify_on_replay_b,status,notes\n'
        '012,NASTRAN-95,fortran,make,golden,yes,yes,yes,yes,PASS,established\n')


def _load():
    spec = importlib.util.spec_from_file_location('fetch_readmit', _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _benchmark(tmp_path: Path):
    """A benchmark folder whose name carries an admission number."""
    dest = tmp_path / '012-thing'
    src = dest / 'thing'
    src.mkdir(parents=True)
    (src / 'Makefile').write_text('all:\n\t@true\n')
    csv_path = tmp_path / 'benchmark.csv'
    csv_path.write_text(_ROW)
    return dest, csv_path


def test_a_failed_readmit_leaves_the_row(tmp_path: Path):
    fetch = _load()
    dest, csv_path = _benchmark(tmp_path)
    fetch.BENCH_CSV = csv_path
    fetch.native = lambda src, args: (False, 'the build failed\n', [])

    rc = fetch.main([str(dest), '--readmit', '--agent-cmd', 'false',
                     '--configure', '', '--build', 'false', '--test', 'false'])

    assert rc == 1
    row = next(csv.DictReader(csv_path.open()))
    assert row['status'] == 'PASS', 'a failed re-prove clobbered the row'
    assert row['language'] == 'fortran', 'the row lost its identifying columns'
    assert row['notes'] == 'established'


def test_a_failed_first_admission_is_still_recorded(tmp_path: Path):
    """The rule is about RE-proving.  A project that was never legal has no
    established state to protect, and its failure IS the corpus's finding."""
    fetch = _load()
    bench = tmp_path / 'benchmark'
    bench.mkdir()
    csv_path = tmp_path / 'benchmark.csv'
    fetch.BENCH = bench
    fetch.BENCH_CSV = csv_path
    fetch.fetch = lambda url, dest: dest.mkdir(parents=True)
    fetch.native = lambda src, args: (False, 'the build failed\n', [])

    rc = fetch.main(['https://example.invalid/proj', '--name', 'proj',
                     '--agent-cmd', 'false'])

    assert rc == 1
    row = next(csv.DictReader(csv_path.open()))
    assert row['status'] == 'REFUSED'
    assert row['number'] == '001'


def test_a_readmit_never_deletes_the_project_it_is_re_proving(tmp_path: Path):
    """An IN-SOURCE build's tree IS the source tree.

    `--tree-subdir .` names the checkout itself, and a generated-build cleanup
    that keyed only on "is there a CMakeCache.txt" fired on it — deleting the
    project it was asked to re-prove.  An in-source build has a cache too, so
    that guard protected exactly the case its comment claimed to.
    """
    fetch = _load()
    # `--readmit` takes a BENCHMARK folder, whose name carries the number.
    dest = tmp_path / '012-proj'
    src = dest / 'proj'
    (src / 'sub').mkdir(parents=True)
    (src / 'sub' / 'a.c').write_text('int main(void){return 0;}\n')
    (src / 'CMakeCache.txt').write_text('an in-source build\n')
    (src / 'CMakeFiles').mkdir()
    (src / 'Makefile').write_text('# the PROJECT\'S OWN Makefile\n')
    (src / 'src_main.c').write_text('int main(void){return 0;}\n')

    fetch.BENCH_CSV = tmp_path / 'benchmark.csv'
    fetch.BENCH_CSV.write_text(_ROW)
    fetch.native = lambda s, a: (False, 'stop here\n', [])

    rc = fetch.main([str(dest), '--readmit', '--tree-subdir', '.', '--agent-cmd', 'false',
                     '--configure', '', '--build', 'false', '--test', 'false'])

    assert rc == 1
    assert (src / 'sub' / 'a.c').is_file(), (
        'the re-prove deleted the project: `--tree-subdir .` was treated as a '
        'generated build directory')
    # The build system's OWN state must go, or the next configure runs against
    # the old answers.
    assert not (src / 'CMakeCache.txt').exists(), (
        'a stale cache survived, so the next configure re-runs the project '
        'against its own previous answers')
    assert not (src / 'CMakeFiles').exists()
    # ...and the project's own files must NOT.
    assert (src / 'Makefile').is_file(), 'a project file was deleted'
    assert (src / 'src_main.c').is_file(), 'a source file was deleted'


def test_an_out_of_source_build_directory_is_still_cleared(tmp_path: Path):
    """The guard must not disable the cleanup it exists to make safe."""
    fetch = _load()
    dest = tmp_path / '012-proj'
    src = dest / 'proj'
    (src / 'sub').mkdir(parents=True)
    (src / 'sub' / 'a.c').write_text('int main(void){return 0;}\n')
    build = src / 'build'
    build.mkdir()
    (build / 'CMakeCache.txt').write_text('a generated build\n')
    stale = build / 'STALE'
    stale.write_text('left by the previous build\n')

    fetch.BENCH_CSV = tmp_path / 'benchmark.csv'
    fetch.BENCH_CSV.write_text(_ROW)
    fetch.native = lambda s, a: (False, 'stop here\n', [])

    fetch.main([str(dest), '--readmit', '--tree-subdir', 'build', '--agent-cmd', 'false',
                '--configure', '', '--build', 'false', '--test', 'false'])

    assert not stale.exists(), 'a generated build directory should be cleared'
    assert (src / 'sub' / 'a.c').is_file()
