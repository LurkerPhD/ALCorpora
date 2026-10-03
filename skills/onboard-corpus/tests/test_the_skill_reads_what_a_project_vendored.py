"""The scope comes from the PROJECT's own statement of what it vendored.

A corpus is vendored together with the third-party libraries its build links.
Which directories those are is not something a consumer should guess at: the
project says so itself, and the onboarding skill reads that statement.

Two sources, preferred in this order because they are the same fact recorded
at different distances:

1. ``.gitmodules``, read through ``git config -f`` — the project's own
   declaration, in git's own format.  Read from the project CHECKOUT, which
   the onboarding run has: it is the run that built it.
2. the build's ``CMakeCache.txt``, whose ``<Name>_SOURCE_DIR`` entries name
   the directories a ``project()`` was declared in and the top-level build
   pulled in — the same answer, recorded by the build, for a tree captured
   without its checkout beside it.

Every other signal was tried against the real corpora and rejected; the
rejections are recorded on :func:`own_roots_for` because each one looks
plausible until the data is in front of you.  The one that matters most:
"compiled into an archive" marks LAPACK's OWN code as foreign, since
``liblapack.a`` is LAPACK's own library.
"""

from __future__ import annotations

from pathlib import Path

from protocolize.onboard.extract import gitmodules_paths, own_roots_for
from protocolize.private_protocol import TREE, CompileEntry, Protocol


def _corpus(tmp_path: Path, vendored: tuple[str, ...]):
    """A project that vendors *vendored* directories beside its own code."""
    root = tmp_path / 'checkout'
    root.mkdir()
    (root / 'Source').mkdir()
    (root / 'Source' / 'main.f').write_text('      subroutine s\n      end\n')
    for rel in vendored:
        d = root / rel / 'SRC'
        d.mkdir(parents=True)
        (d / 'lib.f').write_text('      subroutine s\n      end\n')
    if vendored:
        text = ''.join(
            f'[submodule "{Path(r).name}"]\n\tpath = {r}\n\turl = x\n'
            for r in vendored)
        (root / '.gitmodules').write_text(text)
    return root


def _proto(root: Path) -> Protocol:
    """A protocol whose units are spelled through the build's overlay."""
    entries = []
    for f in sorted(root.rglob('*.[fF]*')):
        rel = f.relative_to(root)
        entries.append(CompileEntry(
            directory=str(f.parent), command=f'gfortran -c {f}',
            file=str(f), output=f'{TREE}/{rel}.o',
            stem=str(rel.with_suffix('')), variant='fortran'))
    return Protocol(compilation=entries, comment='t')


def test_git_reports_what_the_project_declares_it_vendored(tmp_path):
    root = _corpus(tmp_path, ('submodules/lapack', 'submodules/metis'))
    assert gitmodules_paths(root) == ['submodules/lapack', 'submodules/metis']


def test_a_project_that_vendors_nothing_declares_nothing(tmp_path):
    """LAPACK and NASTRAN-95: no ``.gitmodules``, so every unit is theirs."""
    root = _corpus(tmp_path, ())
    assert gitmodules_paths(root) == []
    assert own_roots_for(_proto(root), tmp_path / 'build', tmp_path,
                         root) == ()


def test_the_vendored_tree_is_not_the_corpus(tmp_path):
    root = _corpus(tmp_path, ('submodules/lapack',))
    proto = _proto(root)
    roots = own_roots_for(proto, tmp_path / 'build', tmp_path, root)

    assert roots == ('Source',)
    kept = [e.stem for e in proto.compilation
            if any(e.stem == r or e.stem.startswith(r + '/') for r in roots)]
    assert kept == ['Source/main']
    assert all('submodules' not in k for k in kept)


def test_a_build_that_copied_the_sources_still_scopes_correctly(tmp_path):
    """The protocol's stems go through an overlay; the declaration does not.

    MYSTRAN's build copies ``Source/`` and ``submodules/`` into its own tree,
    so a unit's stem is ``src_overlay/submodules/lapack/SRC/lib`` while the
    project declares ``submodules/lapack``.  Matching whole path SEGMENTS is
    what survives that; a prefix compare on the full stem would not.
    """
    root = _corpus(tmp_path, ('submodules/lapack',))
    proto = _proto(root)
    overlaid = [
        CompileEntry(directory=e.directory, command=e.command, file=e.file,
                     output=e.output,
                     stem='src_overlay/' + e.stem, variant='fortran')
        for e in proto.compilation]
    roots = own_roots_for(Protocol(compilation=overlaid), tmp_path / 'b',
                          tmp_path, root)
    assert roots == ('src_overlay/Source',)
