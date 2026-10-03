"""A flat build must be able to MAKE the sources it compiles.

A generated input is a source a corpus does not ship: LAPACK compiles 3,489
`_64` mirrors that do not exist until a custom command makes them, MYSTRAN
570.  The protocol records each one with the command that produces it and
the files it depends on, and the flat generator has to turn that back into a
rule — otherwise a replay compiles a file that is not there.

The failure is the worst shape: it does not look like a protocol gap.  The
protocol is complete, `problems()` is `[]`, the generated CMakeLists is
plausible, and the build dies on one missing file among thousands — a
symptom that names the corpus rather than the generator.

The corpus here GENERATES a source and then compiles it, which is the whole
of what a generated input is.  It is small on purpose: the property is
"a rule exists and runs", and a large third-party tree is not needed to
observe it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from protocolize.onboard.extract import extract_and_prove
from protocolize.private_protocol import TREE, expand_tree

pytestmark = pytest.mark.skipif(
    not (shutil.which('cmake') and shutil.which('ninja')),
    reason='needs cmake and ninja')


def _make_generating_corpus(root: Path) -> Path:
    """A corpus whose build MAKES one of the sources it compiles.

    `gen.py` writes `computed.f90`; the library compiles both `computed.f90`
    and a hand-written `main.f90`.  Nothing ships `computed.f90`.
    """
    src = root / 'source'
    src.mkdir(parents=True)
    (src / 'main.f90').write_text(
        '      program main\n'
        '        call show()\n'
        '      end program main\n')
    (src / 'gen.py').write_text(
        'import sys, pathlib\n'
        'pathlib.Path(sys.argv[1]).write_text(\n'
        '    "      subroutine show()\\n"\n'
        '    "        write(*,*) 6 * 7\\n"\n'
        '    "      end subroutine show\\n")\n')
    (root / 'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.16)\n'
        'project(gen LANGUAGES Fortran)\n'
        f'set(COMPUTED "{src}/computed.f90")\n'
        'add_custom_command(OUTPUT "${COMPUTED}"\n'
        '  COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/source/gen.py"'
        ' "${COMPUTED}"\n'
        '  DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/source/gen.py")\n'
        f'add_executable(demo "{src}/main.f90" "${{COMPUTED}}")\n')
    return src


@pytest.fixture(scope='module')
def replay(tmp_path_factory):
    """A protocol from a real generating build, replayed by the PROOF.

    The proof is the replay — asking for the protocol runs it, and refuses
    to return anything the rebuild did not match.  A second, hand-rolled
    replay here would be a second way to turn a protocol into a build, and
    the two would drift; this fixture reads the tree the proof built in.
    """
    root = tmp_path_factory.mktemp('gen_corpus')
    _make_generating_corpus(root)
    build = root / 'build'
    subprocess.run(['cmake', '-S', str(root), '-B', str(build), '-G', 'Ninja',
                    '-DCMAKE_Fortran_COMPILER=gfortran'],
                   check=True, capture_output=True)
    subprocess.run(['cmake', '--build', str(build)], check=True,
                   capture_output=True)
    work = tmp_path_factory.mktemp('gen_work')
    proto, report = extract_and_prove(build, tree_root=root, work_root=work,
                                      jobs=4)
    assert report.ok, report.summary()
    return root, work / 'tree', proto, report


def test_the_extraction_records_the_generated_source(replay):
    """The premise: this corpus HAS a generated input to get wrong."""
    _root, _fresh, proto, _r = replay
    assert proto.generated_inputs, 'nothing was recorded as generated'
    assert not any(TREE not in g['output'] and not g['output'].startswith('/')
                   for g in proto.generated_inputs)


def test_the_replay_makes_the_generated_source_itself(replay):
    """The fresh tree has the RECIPE and not the OUTPUT.

    That is the whole point: a replay must be able to produce what the
    original build produced, from the corpus's own sources.
    """
    _root, fresh, _proto, _r = replay
    text = (fresh / 'CMakeLists.txt').read_text(encoding='utf-8')
    assert 'add_custom_command' in text, (
        'the flat build emits no rule for the sources it must generate, so '
        'it compiles a file that does not exist')


def test_the_replayed_program_runs_and_agrees(replay):
    """The proof compares a build's SYMBOLS, which is the strongest thing
    that can be compared across two build systems — but it cannot tell
    whether the program works.  This asks the one question it does not.

    Everything before it is the proof's: the tree was built from the protocol
    alone, the generated source was made rather than shipped, and the
    products matched.  What is left is whether the thing RUNS.
    """
    _root, fresh, _proto, report = replay
    assert report.ok, report.summary()
    # The product lands where its recorded link command writes it, re-rooted
    # into this tree — the build is a verbatim replay.
    exe = next(fresh.rglob('demo'))
    got = subprocess.run([str(exe)], capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == '42', got.stdout
