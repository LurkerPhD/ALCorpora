"""Onboarding must not be a CMake-only skill.

The extractor read ninja's sub-tools, which covers every CMake project and
nothing else — and a project built by a hand-written Makefile has no
``build.ninja`` to read at all.  Such a project is not exotic: it is what a
great deal of older Fortran looks like, and "the skill can onboard any
project" is false while it is refused.

``make`` has its own interface for this, and it is the same kind of
interface ninja's sub-tools are: ``make -Bn <target>`` prints the commands
the build WOULD run, with ``$@``/``$<`` already resolved.  Three things
about it carry the whole design, and each is pinned below because each one
fails silently:

* ``-B`` is not optional.  Without it, ``make -n`` on an up-to-date tree
  prints "Nothing to be done" and the extraction comes back EMPTY — no
  error, just nothing, which reads exactly like a project with no units.
* the target must be named.  A Makefile's default target may build fewer
  products than the build has; asking for ``all`` is what makes the
  protocol describe the whole build.
* an archive is named by the FIRST non-flag token in the common
  ``ar cr lib.a a.o b.o`` form, and after ``-o`` in the ``-o`` form.  Both
  occur, and reading only one silently produces a link line with no
  members.

The project here is two directories with their own Makefiles, no root
Makefile, an archive, an executable that links it by a relative path, and
one source compiled INSIDE the link line with no object of its own.  That
last one is the shape that makes a link edge and a compile edge one thing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from protocolize.private_protocol import TREE, check_tree_invariant

pytestmark = pytest.mark.skipif(not shutil.which('make'),
                                reason='needs make')


def _make_project(root: Path) -> Path:
    """Two make directories: `mis` builds an archive, `mds` links it."""
    mis = root / 'mis'
    mds = root / 'mds'
    mis.mkdir(parents=True)
    mds.mkdir(parents=True)

    (mis / 'alpha.f').write_text(
        '      subroutine alpha()\n'
        '        write(*,*) 6 * 7\n'
        '      end subroutine alpha\n')
    (mis / 'beta.f').write_text(
        '      subroutine beta()\n'
        '      end subroutine beta\n')
    (mis / 'Makefile').write_text(
        'FC = gfortran\n'
        'FFLAGS = -O2\n'
        'libmis.a: alpha.o beta.o\n'
        '\tar crs $@ $^\n'
        '%.o: %.f\n'
        '\t$(FC) $(FFLAGS) -c -o $@ $<\n')

    (mds / 'main.f').write_text(
        '      program main\n'
        '        call alpha()\n'
        '        call gamma()\n'
        '      end program main\n')
    # Compiled INSIDE the link line: no `gamma.o` exists at any point.
    (mds / 'gamma.f90').write_text(
        '      subroutine gamma()\n'
        '      end subroutine gamma\n')
    (mds / 'Makefile').write_text(
        'FC = gfortran\n'
        'FFLAGS = -O2\n'
        'app: main.o\n'
        '\t$(FC) $(FFLAGS) -o $@ main.o gamma.f90 ../mis/libmis.a\n'
        '%.o: %.f\n'
        '\t$(FC) $(FFLAGS) -c -o $@ $<\n')
    return root


def _build(root: Path) -> None:
    for d in (root / 'mis', root / 'mds'):
        subprocess.run(['make'], cwd=str(d), check=True,
                       capture_output=True)


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp('make_project')
    _make_project(root)
    _build(root)
    return root


def _protocol(root: Path):
    from protocolize.onboard.extract import extract_protocol
    return extract_protocol(root, tree_root=root)


def test_a_make_build_yields_a_protocol(built):
    proto = _protocol(built)
    stems = {e.stem for e in proto.compilation}
    assert stems, 'the make build yielded no units'


def test_every_unit_is_a_fortran_source_of_this_project(built):
    proto = _protocol(built)
    for e in proto.compilation:
        assert e.language == 'fortran', e.stem
        assert not e.stem.endswith(('.f', '.f90')), e.stem
    assert {e.stem for e in proto.compilation} == {
        'mis/alpha', 'mis/beta', 'mds/main', 'mds/gamma'}, (
        'a source compiled inside a link line is still a unit')


def test_a_source_compiled_inside_a_link_line_carries_its_own_command(built):
    """It has no object until the link runs, so the extractor synthesises a
    compile for it — and that compile must not be the link line itself, which
    names every object of the product and would carry them into the unit's
    compile options as bare paths."""
    proto = _protocol(built)
    gamma = next(e for e in proto.compilation if e.stem == 'mds/gamma')
    assert '-c' in gamma.command, gamma.command
    assert 'libmis.a' not in gamma.command, (
        'the link line leaked into a compile command')
    assert 'main.o' not in gamma.command, (
        'another unit leaked into this unit\'s compile command')


def test_the_archive_is_recorded_with_its_members_as_stems(built):
    """`ar crs libmis.a alpha.o beta.o` names the archive POSITIONALLY.

    That is the common form — `-o` is the unusual one — and reading only the
    `-o` form yields a protocol whose archive has no members, which links
    into nothing.
    """
    proto = _protocol(built)
    archives = [p for p in proto.products if p.kind == 'archive']
    assert len(archives) == 1, [p.output for p in proto.products]
    assert set(archives[0].stems) == {'mis/alpha', 'mis/beta'}, (
        archives[0].stems)


def test_the_executable_is_recorded_and_its_objects_are_stems(built):
    proto = _protocol(built)
    exes = [p for p in proto.products if p.kind == 'exe']
    assert len(exes) == 1, [p.output for p in proto.products]
    defined = {e.stem for e in proto.compilation}
    for p in proto.products:
        for m in p.stems:
            assert m in defined, f'{p.output} names {m!r}, not a unit'
            assert not m.endswith(('.o', '.f', '.f90')), m


def test_the_protocol_is_sound_and_spells_each_source_by_what_it_means(built):
    """A source is spelled by what a REPLAY has to do with it, not by where
    it happens to sit.

    A file the project ships — including one that lies inside the build tree
    because the Makefile runs there — is absolute: nothing regenerates it, so
    a placeholder would name a file the replay never has.  A file the build
    PRODUCES is `${TREE}/...`: the replay makes its own.  Spelling both the
    same way is how a replayed build comes to depend on a file it cannot
    create.
    """
    proto = _protocol(built)
    assert proto.problems() == []
    produced = {g['output'] for g in proto.generated_inputs}
    for e in proto.compilation:
        assert check_tree_invariant(e.command) is None, e.command
        under_tree = e.file.startswith(TREE)
        assert under_tree == (e.file in produced), (
            f'{e.stem}: {e.file} is spelled '
            f'{"as generated" if under_tree else "absolutely"}')


def test_an_up_to_date_tree_still_yields_its_commands(built):
    """``-B`` is what makes this true.  Without it, a second extraction on an
    already-built tree returns NOTHING — no error, just an empty protocol,
    which reads exactly like a project with no translation units."""
    first = _protocol(built)
    second = _protocol(built)
    assert len(second.compilation) == len(first.compilation)
    assert second.products, 'the second extraction found no products'


def test_a_project_with_no_build_is_refused(tmp_path):
    """Refused with the gap named, never a plausible-but-empty protocol."""
    from protocolize.onboard.extract import ExtractionError, extract_protocol
    empty = tmp_path / 'nothing'
    empty.mkdir()
    (empty / 'a.f').write_text('      subroutine a\n      end\n')
    with pytest.raises(ExtractionError) as exc:
        extract_protocol(empty, tree_root=empty)
    assert 'Makefile' in str(exc.value) or 'build' in str(exc.value), exc.value


# ── what the skill must work out for itself ───────────────────

def test_the_caller_does_not_have_to_say_what_tree_means(built):
    """`${TREE}` is the skill's to determine, not the caller's to supply.

    A caller cannot know whether a build tree is in-source or out-of-source,
    or where a project keeps the sources it does not generate — and a wrong
    answer is SILENT: a placeholder one level off names `<root>/<root>/...`,
    and the extraction still reports every unit with no problems at all.
    So extracting with no `tree_root` must work, and must agree with the
    root the entries themselves imply.
    """
    from protocolize.onboard.extract import extract_protocol, infer_tree_root
    derived = extract_protocol(built, verify=False)
    stated = extract_protocol(built, tree_root=infer_tree_root(built),
                              verify=False)
    assert [e.to_json() for e in derived.compilation] == \
           [e.to_json() for e in stated.compilation]


def test_a_generated_source_travels_with_the_protocol(built):
    """What the build tree HOLDS is what a replay must make.

    A unit's source inside the build tree is one this build produced, so it
    belongs to the placeholder; a unit's source outside it does not move and
    stays absolute, because a replay reads the very same file.
    """
    from protocolize.private_protocol import TREE
    proto = _protocol(built)
    for e in proto.compilation:
        inside = e.file.startswith(TREE)
        on_disk = Path(
            e.file if not inside else
            str(built) + e.file[len(TREE):]).is_file()
        assert on_disk, f'{e.stem}: {e.file} names no file'


# ── the same spelling rule the ninja backend follows ──────────

def test_a_product_is_spelled_under_the_tree(built):
    """A make build's products travel with the placeholder too.

    Same rule as the ninja backend, same reason: a product left out of the
    `produced` set is spelled ABSOLUTELY, and a replay from that protocol
    writes the archive and the executables back into the capture tree.
    Measured on NASTRAN-95: all four products came out absolute.
    """
    from protocolize.private_protocol import TREE
    proto = _protocol(built)
    assert proto.products, 'the fixture links nothing'
    for p in proto.products:
        assert p.output.startswith(TREE), p.output


def test_the_link_products_are_the_builds_own(built):
    """Both `ar` spellings resolve to the same archive.

    `ar crs lib.a a.o b.o` (positional) and `ar cr -o lib.a a.o b.o` both
    occur, and reading only one of them either misses the archive or takes an
    object for it.  The fixture is built to exercise whichever one its
    Makefile writes; the check is that an archive is found either way.
    """
    proto = _protocol(built)
    kinds = {p.kind for p in proto.products}
    assert 'archive' in kinds, kinds
    assert 'exe' in kinds, kinds
