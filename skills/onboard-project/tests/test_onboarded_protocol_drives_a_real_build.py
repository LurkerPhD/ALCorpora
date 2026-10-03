"""The closure: a protocol read from a real build drives a real build.

Two properties have been tested apart until now — the extractor reads a
build (``test_onboard_extracts_a_protocol_from_a_build``), and the flat
generator turns a protocol into a CMakeLists
(``test_flat_cmake_follows_the_state``).  Neither crosses the join, and the
join is where the two disagree.

The disagreement is not hypothetical.  The flat generator's own fixture
builds its entries with commands like ``gfortran -c main.f`` — no
placeholder, no include path, nothing a real build system writes.  Every
assertion it makes is therefore about a command shape that never occurs,
and it passes while the real path is broken:

* a real build records commands with ABSOLUTE paths, which the extractor
  re-roots at ``${TREE}``.  Those placeholders travel into the flags the
  flat generator emits, and CMake reads ``${TREE}`` as one of its OWN
  variables — empty — so every include path silently loses its root;
* a real CMake Fortran build compiles in TWO steps, and the second step's
  command carries ``-fpreprocessed``, which is true of the intermediate it
  consumes and false of the source a replay compiles.

So this test builds a corpus, extracts from it, generates a fresh build
from the protocol alone, and then ASKS THAT BUILD what it did — an
observation about the compile line, not about the text of a file.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

import shutil
import subprocess

import pytest

from protocolize.onboard.extract import extract_and_prove
from protocolize.private_protocol import TREE
from test_onboard_extracts_a_protocol_from_a_build import (_configure,
                                                          _make_corpus)

pytestmark = pytest.mark.skipif(
    not (shutil.which('cmake') and shutil.which('ninja')),
    reason='needs cmake and ninja')


@pytest.fixture(scope='module')
def replay(tmp_path_factory):
    """A protocol from a real build, replayed into a fresh tree.

    Returns ``(fresh_tree, build_dir, generated_text)``.  The fresh tree
    holds the CORPUS's sources and nothing else: the build directory is not
    copied, because reproducing it from the protocol is the whole point.
    """
    root = tmp_path_factory.mktemp('replay')
    _make_corpus(root)
    build_dir = _configure(root)

    # The proof IS the replay, so this fixture does not perform a second one.
    # It asks for the protocol the ordinary way — which now means the
    # extraction ran its own rebuild and refused to return anything if that
    # rebuild did not match — and then reads the tree the proof built in.
    # A separate hand-rolled replay here would be a SECOND way to generate a
    # build from a protocol, and the two would drift.
    work = tmp_path_factory.mktemp('replay_work')
    proto, report = extract_and_prove(build_dir, tree_root=root,
                                      work_root=work)
    assert report.ok, report.summary()

    tree = work / 'tree'
    return tree, work / 'build', (tree / 'CMakeLists.txt').read_text(
        encoding='utf-8'), report


def test_no_placeholder_survives_into_the_generated_build(replay):
    """CMake spells its own variables ``${...}``, so a surviving ``${TREE}``
    is not an inert marker: it is an empty variable, and the path it was
    rooted at is gone.

    Asserted on ``${TREE}`` rather than on ``${`` — the generated file has
    plenty of the latter, because the generator's OWN variables are spelled
    the same way.
    """
    _fresh, _build, text, _r = replay
    assert TREE not in text, (
        'the generated build still holds the placeholder, which CMake will '
        'read as one of its own (empty) variables')


def test_every_directory_the_build_asks_for_is_expanded(replay):
    """`${TREE}` reaching CMake is not an inert marker — it is an EMPTY
    variable, so a path keeps its shape and loses its root: `/BLAS/SRC`
    instead of `<tree>/BLAS/SRC`.

    A directory is where this hides best.  `file(MAKE_DIRECTORY "/SRC")`
    does not fail the build in a way that names the flag or the directory;
    it fails to create a directory the compiler needs, and the compiler
    then reports a missing module file.

    Asserted over every line of the generated file rather than over the one
    that was seen to break, because the placeholder can enter through any
    path that skips the expansion helper.
    """
    _fresh, _build, text, _r = replay
    assert TREE not in text
    for line in text.splitlines():
        if 'MAKE_DIRECTORY' not in line:
            continue
        for quoted in line.split('"')[1::2]:
            assert not quoted.startswith('/') or 'TREE' not in quoted, line
            assert quoted.strip() != '', line


def test_the_compiled_include_dirs_are_real_directories(replay):
    """Ask the build, not the file.

    A joined flag list reads as ONE option, so the include path becomes a
    string like ``/source -fpreprocessed`` — an argument that names no
    directory, which the compiler reports as a warning and otherwise
    ignores, leaving the build looking successful.
    """
    fresh, build, _text, _r = replay
    obj = next(p for p in fresh.rglob('*.o') if 'main' in p.name)
    cmd = _compile_command(build, obj)
    # The command is replayed VERBATIM, wrapped in `sh -c "..."`, so the
    # compile line is the inner payload: split once for the wrapper, once for
    # the compiler.
    argv = shlex.split(cmd)
    payload = argv[argv.index('-c') + 1] if '-c' in argv else cmd
    actual = [a[2:] for a in shlex.split(payload) if a.startswith('-I')]
    assert actual, f'no include path reached the compiler: {cmd}'
    for d in actual:
        assert Path(d).is_dir(), (
            f'{d!r} is on the compile line and is not a directory — a flag '
            f'list joined into one option')


def test_a_two_step_unit_is_replayed_in_two_steps(replay):
    """A scanned source is compiled in two edges; the replay does both.

    CMake compiles a scanned Fortran source in TWO steps, and the compile
    edge consumes a `<src>-pp.<ext>` INTERMEDIATE the preprocess edge made —
    carrying `-fpreprocessed`, which is true of that intermediate.  A replay
    that emits only the compile command names a file nothing created; this
    asserts the intermediate is PRODUCED, by the command that makes it.
    """
    _fresh, _build, text, _r = replay
    assert '-fpreprocessed' in text, (
        'the two-step unit was collapsed to one step, so the compile command '
        'consumes an intermediate nothing produces')
    pp = [ln for ln in text.splitlines() if '-pp.f90' in ln and 'OUTPUT' in ln]
    assert pp, 'no rule produces the preprocess intermediate'


def test_the_replay_runs_and_agrees_with_the_corpus(replay):
    """The end of the closure: a program built from the protocol alone."""
    fresh, build, _text, _r = replay
    # The product lands where its recorded link command writes it, re-rooted
    # into THIS tree — the build is a verbatim replay, so nothing about where
    # an artifact goes is re-derived.
    exe = next(fresh.rglob('demo'))
    got = subprocess.run([str(exe)], capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == '84', (
        f'the replayed program printed {got.stdout.strip()!r}')


def _compile_command(build: Path, obj: Path) -> str:
    """What the build system says it ran to make *obj*."""
    rel = str(obj)   # the custom command's OUTPUT is an absolute path
    out = subprocess.run(['ninja', '-C', str(build), '-t', 'commands', rel],
                         capture_output=True, text=True, check=True).stdout
    return out.strip().splitlines()[-1]
