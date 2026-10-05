"""A generated source must be spelled under ${TREE}, not absolutely.

The whole point of the placeholder is that a protocol captured in ONE tree
can be replayed in ANOTHER.  `Toolchain.in_tree_space` decides a path's
spelling by asking whether the path is something the build PRODUCES: a
produced path is one the replay must MAKE, so it lives under the (new) tree;
anything else is something the replay READS, so it keeps its absolute path.

Measured on the real LAPACK tree: the extractor emitted all 3489 generated
sources AND all 65 products as absolute paths under the CAPTURE tree.  A
replay from that protocol writes 3489 files into the project it was supposed
to be proving something about — the one thing a self-check must never do.

The cause is an ordering one: `_read_ninja` computed `generated` BEFORE
assigning `tc.produced`, and `_generated` is itself a client of the speller,
so it asked `produced` while it was still the empty default and got "not
produced" for every output.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from alcorpora.private_protocol import TREE, expand_tree

pytestmark = pytest.mark.skipif(
    not (shutil.which('cmake') and shutil.which('ninja')),
    reason='needs cmake and ninja')


def _make_corpus(root: Path) -> None:
    """A project that GENERATES a source, then compiles it.

    The generated file is the case that matters: it is the one the replay
    must make for itself rather than read.
    """
    src = root / 'source'
    src.mkdir(parents=True)
    (src / 'seed.f90.in').write_text(
        '      subroutine doubled(n, r)\n'
        '        integer n\n'
        '        real r\n'
        '        r = 2.0 * n\n'
        '      end subroutine doubled\n')
    (src / 'main.f90').write_text(
        '      program main\n'
        '        real r\n'
        '        call doubled(2, r)\n'
        '        write(*,*) r\n'
        '      end program main\n')
    (root / 'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.16)\n'
        'project(gen LANGUAGES Fortran)\n'
        f'set(IN "{src}/seed.f90.in")\n'
        'set(OUT "${CMAKE_CURRENT_BINARY_DIR}/made.f90")\n'
        'add_custom_command(OUTPUT "${OUT}"\n'
        '  COMMAND ${CMAKE_COMMAND} -E copy "${IN}" "${OUT}"\n'
        '  DEPENDS "${IN}")\n'
        f'add_executable(demo "{src}/main.f90" "${{OUT}}")\n')
    (root / 'build').mkdir()


@pytest.fixture(scope='module')
def extracted(tmp_path_factory):
    root = tmp_path_factory.mktemp('gen_corpus')
    _make_corpus(root)
    build = root / 'build'
    subprocess.run(['cmake', '-S', str(root), '-B', str(build), '-G', 'Ninja',
                    '-DCMAKE_Fortran_COMPILER=gfortran'],
                   check=True, capture_output=True)
    subprocess.run(['cmake', '--build', str(build)],
                   check=True, capture_output=True)
    from alcorpora.onboard.extract import extract_protocol
    return extract_protocol(build, comment='probe')


def test_the_generated_source_is_spelled_under_the_tree(extracted):
    """The property the whole placeholder exists for."""
    assert extracted.generated_inputs, 'the fixture generated nothing'
    for g in extracted.generated_inputs:
        assert g['output'].startswith(TREE), (
            f'{g["output"]!r} is not re-rooted, so a replay would write it '
            f'back into the tree the protocol was captured from')
        assert '/tmp/' not in g['output'], 'an absolute path survived'


def test_a_product_is_spelled_under_the_tree(extracted):
    """Same rule for the link products."""
    assert extracted.products, 'no products'
    for p in extracted.products:
        assert p.output.startswith(TREE), p.output


def test_nothing_under_the_capture_tree_is_spelled_absolutely(extracted):
    """No path in the whole protocol points back into the capture tree."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d)
        for g in extracted.generated_inputs:
            out = Path(expand_tree(g['output'], tree))
            # Expanded against a DIFFERENT tree, the output lands there.
            # That is what makes the protocol replayable at all.
            assert str(tree) in str(out), out
            assert 'gen_corpus' not in str(out), (
                f'{out} still names the capture tree')


def test_a_source_the_project_ships_keeps_its_absolute_path(extracted):
    """The other side of the rule, so the first one is not just "root it".

    A file the replay READS does not travel with the placeholder — it is in
    the project, and re-rooting it would name a copy that does not exist.
    """
    files = [e.file for e in extracted.compilation]
    read_from_outside = [f for f in files if not f.startswith(TREE)]
    assert read_from_outside, (
        'every source was re-rooted, so the distinction the speller draws '
        'is not being drawn at all')


# ── the root itself ───────────────────────────────────────────

def test_a_relative_root_is_refused_not_silently_absolute(tmp_path):
    """A relative root spells EVERY path absolute, and says nothing.

    The speller asks `absolute.relative_to(root)`; against a relative root
    that raises for every path, so it takes the "outside the tree" branch and
    the protocol comes out entirely absolute and entirely well-formed.
    Measured on the real LAPACK tree: 3489 generated sources and 65 products,
    none of them replayable, no error anywhere.
    """
    from alcorpora.onboard.extract import ExtractionError, Toolchain
    with pytest.raises(ExtractionError) as exc:
        Toolchain(build=Path('some/relative/build'),
                  root=Path('some/relative/build'))
    assert 'relative' in str(exc.value).lower()


def test_infer_tree_root_answers_absolutely():
    """It is the answer to "what does ${TREE} mean" — it must be usable."""
    from alcorpora.onboard.extract import infer_tree_root
    got = infer_tree_root(Path('.'))
    assert got.is_absolute(), got
