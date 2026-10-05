"""Onboarding: read a protocol out of a build the corpus already has.

A new corpus must cost as little as possible to add.  The whole design
rests on that: the skill runs ONCE, produces a protocol, and everything
after it is the flow's — no per-corpus Python, no build script written by
hand.

So the extractor reads what the BUILD SYSTEM already knows, through the
build system's own sub-tools, and never parses a manifest itself:

* ``ninja -t targets all``  — enumerate, and name each edge's rule
* ``ninja -t query``        — a target's inputs
* ``ninja -t commands``     — the command a rule runs
* ``ninja -t compdb``       — the compile database, cleaned by CMake

The tests build a SMALL corpus with real cmake+ninja rather than using one
of the shipped corpora: onboarding must be verifiable without a large
third-party tree, and a small build exercises the same sub-tools.

Two properties matter more than coverage:

* the extracted protocol must describe the same units the build compiles —
  not the files a directory scan finds, because those differ (a source that
  no target references is in the tree and not in the build);
* `$` must be escaped when a recorded command is re-rooted, or a protocol
  captured in one tree writes its generated sources back into that tree.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from alcorpora.private_protocol import TREE, Protocol, check_tree_invariant

pytestmark = pytest.mark.skipif(
    not (shutil.which('cmake') and shutil.which('ninja')),
    reason='needs cmake and ninja')


def _make_corpus(root: Path) -> Path:
    """A tiny Fortran project: a module, a user of it, and a program."""
    src = root / 'source'
    src.mkdir(parents=True)
    (src / 'm_mod.f90').write_text(
        '      module m_alpha\n'
        '        integer, parameter :: answer = 42\n'
        '      end module m_alpha\n')
    (src / 'user.f90').write_text(
        '      subroutine show(n)\n'
        '        use m_alpha\n'
        '        integer n\n'
        '        write(*,*) n * answer\n'
        '      end subroutine show\n')
    (src / 'main.f90').write_text(
        '      program main\n'
        '        call show(2)\n'
        '      end program main\n')
    (root / 'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.16)\n'
        'project(demo LANGUAGES Fortran)\n'
        f'add_library(alpha STATIC "{src}/m_mod.f90" "{src}/user.f90")\n'
        f'add_executable(demo "{src}/main.f90")\n'
        'target_link_libraries(demo PRIVATE alpha)\n')
    return src


def _configure(root: Path) -> Path:
    build = root / 'build'
    subprocess.run(['cmake', '-S', str(root), '-B', str(build), '-G', 'Ninja',
                    '-DCMAKE_Fortran_COMPILER=gfortran'],
                   check=True, capture_output=True)
    subprocess.run(['cmake', '--build', str(build)],
                   check=True, capture_output=True)
    return build


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp('onboard_project')
    _make_corpus(root)
    return root, _configure(root)


# ── the compile half ──────────────────────────────────────────

def test_it_reads_the_units_the_build_compiles(built):
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    stems = {e.stem for e in proto.compilation}
    assert len(stems) == 3, f'expected 3 units, got {sorted(stems)}'


def test_it_does_not_scan_for_sources_the_build_ignores(built):
    """The build is the authority, not the directory.

    A file in the tree that no target references is not a translation unit;
    a scan would include it, and then the flow would build something the
    corpus does not.
    """
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    (root / 'source' / 'orphan.f90').write_text(
        '      subroutine nobody\n      end subroutine nobody\n')
    proto = extract_protocol(build, tree_root=root)
    assert 'source/orphan' not in {e.stem for e in proto.compilation}


def test_every_entry_carries_a_usable_command(built):
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    for e in proto.compilation:
        assert e.command, f'{e.stem} has no command'
        assert e.file, f'{e.stem} names no source'
        assert 'gfortran' in e.command or 'f95' in e.command


def test_every_unit_is_recorded_once(built):
    """The protocol keys a unit by its stem, so no stem appears twice."""
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    stems = [e.stem for e in proto.compilation]
    assert sorted(stems) == sorted(set(stems))


# ── the stem identity ─────────────────────────────────────────

def test_the_stem_has_no_extension(built):
    """`isamax.f` and `isamax.cpp` must be one unit, so the key carries none."""
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    for e in proto.compilation:
        assert not e.stem.endswith(('.f', '.f90', '.cpp')), e.stem


# ── the products ──────────────────────────────────────────────

def test_it_records_the_link_products(built):
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    kinds = {p.kind for p in proto.products}
    assert 'exe' in kinds, 'the executable was not recorded'
    assert 'archive' in kinds, 'the archive was not recorded'


def test_a_products_members_are_stems_so_a_flip_follows(built):
    """The property that makes the state switch work; see flat_cmake."""
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    defined = {e.stem for e in proto.compilation}
    for p in proto.products:
        for m in p.stems:
            assert m in defined, f'{p.output} names {m!r}, which is not a unit'
            assert not m.endswith(('.o', '.f', '.f90')), (
                f'member {m!r} carries an extension, so it cannot follow a flip')


# ── the module graph ──────────────────────────────────────────

def test_it_records_who_provides_and_uses_each_module(built):
    """A converted unit emits no .mod, so the graph must be known up front."""
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    mod = next(e for e in proto.compilation if 'm_mod' in e.stem)
    assert 'm_alpha' in [m.lower() for m in mod.provides]
    user = next(e for e in proto.compilation if 'user' in e.stem)
    assert 'm_alpha' in [m.lower() for m in user.uses]


# ── the protocol must be sound and movable ────────────────────

def test_the_extracted_protocol_has_no_problems(built):
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    assert proto.problems() == []


def test_recorded_commands_are_re_rooted_to_the_placeholder(built):
    """What a command WRITES goes under the placeholder; what it READS does not.

    Both halves are load-bearing, and the second is easy to get wrong in the
    other direction: replacing the root as a substring rewrites every path in
    the line, so a header in a directory the build generates at CONFIGURE time
    — or a third-party library, or any resource that is not a stem — came out
    as `${TREE}/...` and sent the replay looking for something nothing
    recreates.  Measured on MYSTRAN: the build died on a missing `metis.h`
    that the capture tree had all along.

    The rule the protocol states: everything that is not a stem is read from
    the origin tree by ABSOLUTE path.
    """
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)

    # The OUTPUTS of a command must not name the capture tree.
    for e in proto.compilation:
        out = shlex.split(e.command)
        if '-o' in out:
            target = out[out.index('-o') + 1]
            assert str(root) not in target, (
                f'{e.stem}: the command writes into the capture tree: {target}')
    # And every command must be a well-formed spelling either way.
    for e in proto.compilation:
        assert check_tree_invariant(e.command) is None, (
            f'{e.stem}: the re-rooted command is not a well-formed spelling')
    # A READ path keeps its absolute spelling, so the replay finds it.
    reads = [tok for e in proto.compilation
             for tok in shlex.split(e.command) if tok.startswith('-I')]
    assert reads, 'the fixture compiles with no include path'
    assert any(tok[2:].startswith('/') and not tok[2:].startswith(TREE)
               for tok in reads), (
        'every -I was re-rooted, so a resource the replay does not produce '
        'would be looked for in a tree that never had it')


def test_it_survives_a_json_round_trip(built, tmp_path):
    from alcorpora.onboard.extract import extract_protocol
    root, build = built
    proto = extract_protocol(build, tree_root=root)
    path = tmp_path / 'p.json.gz'
    proto.save(path)
    back = Protocol.load(path)
    assert {e.stem for e in back.compilation} == \
        {e.stem for e in proto.compilation}
    assert len(back.products) == len(proto.products)


# ── a generated source inside a dependency graph ──────────────

def _make_gensrc_corpus(root: Path) -> Path:
    """A corpus whose generated source is a SOURCE of a target that LINKS a lib.

    That is the shape that makes ``ninja -t commands`` answer more than one
    line per target.  A generated source compiled straight into an executable
    that links a library gets an ORDER-ONLY dependency on that library (the
    library's own generated files must exist first), so the request's closure
    includes the library's edges and the answer is longer than the target
    list.  Measured: this shape gives 5 lines for one requested target; a
    static library built FROM the generated sources gives one line each, which
    is why a fixture built that way does not exercise the defect.
    """
    src = root / 'source'
    src.mkdir(parents=True)
    (src / 'main.f90').write_text(
        '      program main\n'
        '        call use_a()\n'
        '        call use_b()\n'
        '      end program main\n')
    (src / 'alpha.f90').write_text(
        '      subroutine al()\n'
        '        write(*,*) 9\n'
        '      end subroutine al\n')
    (root / 'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.16)\n'
        'project(gen LANGUAGES Fortran)\n'
        f'add_custom_command(OUTPUT "{src}/gen1.f90"\n'
        f'  COMMAND cmake -E copy "{src}/tpl1.f90" "{src}/gen1.f90"\n'
        f'  DEPENDS "{src}/tpl1.f90")\n'
        f'add_custom_command(OUTPUT "{src}/gen2.f90"\n'
        f'  COMMAND cmake -E copy "{src}/tpl2.f90" "{src}/gen2.f90"\n'
        f'  DEPENDS "{src}/tpl2.f90")\n'
        f'add_library(alpha STATIC "{src}/alpha.f90")\n'
        f'add_executable(gen "{src}/main.f90" "{src}/gen1.f90" '
        f'"{src}/gen2.f90")\n'
        'target_link_libraries(gen PRIVATE alpha)\n')
    (src / 'tpl1.f90').write_text(
        '      subroutine use_a()\n        write(*,*) 1\n'
        '      end subroutine use_a\n')
    (src / 'tpl2.f90').write_text(
        '      subroutine use_b()\n        write(*,*) 2\n'
        '      end subroutine use_b\n')
    return root


def test_a_generated_source_in_a_graph_is_still_captured(tmp_path):
    """The command captured for a generated source is the one that MAKES it.

    Measured on a real corpus (LAPACK): one generated source's ``-t commands``
    returns 838 lines, its own edge last, so a count check refuses a tree the
    extractor could read.  The pairing must be the tail, and each command must
    name the output it is paired with.
    """
    from alcorpora.onboard.extract import extract_protocol

    root = _make_gensrc_corpus(tmp_path)
    build = _configure(root)

    proto = extract_protocol(build, tree_root=root)

    gens = {g['output'] for g in proto.generated_inputs}
    assert any(g.endswith('gen1.f90') for g in gens), sorted(gens)
    assert any(g.endswith('gen2.f90') for g in gens), sorted(gens)
    # Each generated source's command must be the one that PRODUCES it.
    for g in proto.generated_inputs:
        produced = g['output'].rsplit('/', 1)[-1]
        assert produced in g['command'], (
            f"the command for {g['output']} does not name it, so it was "
            f"paired with another target's edge: {g['command']}")


def test_a_nested_shell_fragment_is_not_treated_as_a_path(tmp_path):
    """A `sh -c "<script>"` fragment is not a path, even when it contains `/`.

    The re-rooter splits a command into tokens and re-roots the ones the build
    PRODUCES.  A nested shell fragment is one such token and contains slashes,
    so it was taken for a relative path and prefixed with the build directory:
    `cd <tree>/build && ...` became `<build>/cd <tree>/build && ...`.  `cd`
    then executed a file that does not exist, the nested generator wrote
    nothing, and the replay died on a generated source nobody had made.

    The fragment must be spelled INSIDE, so its produced paths are re-rooted
    and its `cd`/`&&` are left for the shell.
    """
    from alcorpora.onboard.extract import Toolchain

    root = (tmp_path / 'tree')
    build = root / 'build'
    gen = root / 'BLAS' / 'x.f'
    gen.parent.mkdir(parents=True)
    gen.write_text('      subroutine x\n      end subroutine x\n')
    build.mkdir(parents=True)

    tc = Toolchain(build=build.resolve(), root=root.resolve())
    tc.produced = frozenset({str(gen.resolve())})

    cmd = (f'cd {build} && cmake -E make_directory {gen.parent} && '
           f'sh -c "cd {build} && cmake -DOUTPUT_FILE={gen} -P gen.cmake"')
    out = tc.in_tree_space_cmd(cmd)

    assert f'{build}/cd' not in out, out
    assert f'-DOUTPUT_FILE={TREE}/BLAS/x.f' in out, out


def test_an_option_value_is_not_prefixed_as_a_path(tmp_path):
    """An option is not a path, even when its value contains slashes.

    ``--tdi=CMakeFiles/x.dir/FortranDependInfo.json`` is a flag whose VALUE is
    a relative path.  The re-rooter's relative-path branch prefixed the WHOLE
    token with the build directory (``<build>/--tdi=…``), so the flag became a
    path that does not exist and the command neither ran nor compiled — the
    replay died on the unit it was for.  Only a produced, ABSOLUTE value may
    be re-rooted.
    """
    from alcorpora.onboard.extract import Toolchain

    root = (tmp_path / 'tree')
    build = root / 'build'
    build.mkdir(parents=True)

    tc = Toolchain(build=build.resolve(), root=root.resolve())
    cmd = ('cmake -E cmake_ninja_depends '
           '--tdi=CMakeFiles/x.dir/FortranDependInfo.json --lang=Fortran '
           '-o CMakeFiles/x.dir/a-pp.f90')
    out = tc.in_tree_space_cmd(cmd)

    assert '--tdi=CMakeFiles/x.dir/FortranDependInfo.json' in out, out
    assert f'{build}/--tdi=' not in out, out
    assert f'{root}/--tdi=' not in out, out
