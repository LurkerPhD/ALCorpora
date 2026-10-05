"""Where a source's converted output goes — one rule, for every corpus.

The pipeline emits ``.cpp``/``.h``; the driver moves them into
``cpp_candidates/<dir>/``; and the emitter spells the include of a callee's
header.  All three must agree, or a file lands where nobody looks.

The rule is **the source's path relative to the root of the tree that
contains it**::

    mis/a.f                    -> ``mis``          (single-level corpus)
    EMG/EMG1/x.f90             -> ``EMG/EMG1``     (nested corpus)
    u.f                        -> ``""``           (at the tree root, flat)

A source's tree is the tree it belongs to, which is what makes the rule work
for a build that has more than one.  LAPACK's extended API *generates* a
``_64`` mirror of every source into its build tree, and those mirrors are
converted too, so one run spans two trees: the corpus
(``benchmarks/lapack/TESTING/LIN/chkxer.f``) and the build
(``build_lapack/TESTING/LIN/xlintstds_64_sources/chkxer_64.f``).  Each file
keeps its path below **its own** root, so both come out at the directory the
build that reads them already has.

**Why not the last directory component.**  ``src.parent.name`` was the
previous rule and it is lossy: LAPACK's ``BLAS/SRC`` and ``SRC`` are two real
directories whose files both collapsed to ``SRC``, so 140 stems were written
where no CMake file could see them.  It is also wrong for MYSTRAN, whose
``Source/EMG/EMG1`` collapsed to ``EMG1`` — that happened to be harmless only
because no two of its 1964 sources share a basename, so nothing ever
collided and the misnaming stayed self-consistent (the build looked in the
same wrong place the pipeline wrote).  Relative-to-root is the same answer
wherever the last component is unambiguous and the right answer where it is
not, and it needs no corpus-specific switch.

**Where the roots come from.**  A corpus that exports ``compile_commands.json``
needs nothing else: the tree roots are derivable from it — the build tree is
the entries' ``directory`` and the corpus root is the common prefix of the
files outside it.  A corpus without one falls back to the common parent of
its declared ``source_dirs``, which is the same value whenever those dirs
describe the corpus honestly.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

__all__ = ['assign_dirs', 'final_dirs', 'stem_key',
           'roots_from_compile_commands', 'find_repo_root',
           'RUN_OUTPUT_DIRS', 'RESULTS_DIR']

#: Where a run of the plan reports.  One name, spelled here, because three
#: places depend on it agreeing: the harness that writes it, the origin that
#: copies goldens out of it, and the onboarding capture, which must NOT see it
#: when it searches the project for the project's own files.
RESULTS_DIR = 'benchmark_results'

#: Directories a run of THIS TOOL leaves inside or beside a project.  A search
#: over the project's tree has to skip them, and the failure when it does not
#: is silent-but-fatal: measured on a hand-written make project, an earlier
#: run's copy of the program config sat under `benchmark_results/work/<case>/`
#: and sorted BEFORE the real `bin/<config>`, so the capture staged the stale
#: copy — and the substitution it then tried to apply was not in it, so every
#: case died naming a file the project had all along.
#:
#: `.work` is the same hazard at a larger scale.  It holds the proof's scratch
#: and, under it, a MIRROR OF THE WHOLE TREE — so a search that descends into
#: it finds a second copy of everything the project has.  Measured while
#: re-proving a minimal sample: the acceptance search found the suite twice
#: (`<tree>/verification` and `<tree>/.work/tree/verification`) and refused,
#: correctly, as ambiguous — the corpus's own rule working as designed against
#: a file that was ours, not its.
RUN_OUTPUT_DIRS = (RESULTS_DIR, '.work')


def assign_dirs(sources: Iterable[Path],
                roots: Iterable[Path | str] | None = None) -> dict[Path, str]:
    """Map each source to its output directory, relative to its own tree root.

    ``''`` is the output root itself — the flat layout a source sitting
    directly at the root of its tree gets.

    *roots* names the trees the sources may live in; a source is placed
    below the **longest** root that contains it (so a nested root wins over
    an enclosing one).  When *roots* is omitted the sources' own common
    parent is used, which is the single-tree case every caller without a
    compile database is in.

    A source under no known root keeps its immediate parent's name — the old
    rule, and the only answer left when nothing says where its tree begins.
    """
    srcs = [Path(s).resolve() for s in sources]
    rs = [Path(r).resolve() for r in (roots or [])]
    if not rs and srcs:
        rs = [Path(os.path.commonpath([str(s.parent) for s in srcs]))]

    out: dict[Path, str] = {}
    for s in srcs:
        root = _owning_root(s, rs)
        if root is None:
            out[s] = s.parent.name
            continue
        rel = s.parent.relative_to(root)
        d = rel.as_posix()
        out[s] = '' if d in ('', '.') else d
    return out


def final_dirs(sources: Iterable[Path],
               roots: Iterable[Path | str] | None = None) -> dict[Path, str]:
    """Map each source to the directory its output is *built* from.

    The same answer as :func:`assign_dirs`, except that a source at its
    tree's root is named by that root's own name rather than ``''``.

    The two differ only for the flat case, and both spellings are needed:
    :func:`assign_dirs` is what the emitter writes (a lone ``u.f`` must stay
    at ``<out>/u.cpp``, which is what callers reading ``<out-dir>/<stem>.cpp``
    expect), while :func:`final_dirs` is what the state keys and the
    ``cpp_candidates`` layout use.
    """
    srcs = [Path(s) for s in sources]
    assigned = assign_dirs(srcs, roots)
    # ``assign_dirs`` keys by resolved path; callers pass whatever spelling
    # they hold, so resolve on the way in to the same keyspace.
    return {s: (assigned[s.resolve()] or s.resolve().parent.name) for s in srcs}


def _owning_root(source: Path, roots: list[Path]) -> Path | None:
    """The longest root that contains *source* (its parent is inside it)."""
    best: Path | None = None
    for r in roots:
        if source.parent == r or r in source.parents:
            if best is None or len(r.parts) > len(best.parts):
                best = r
    return best


def roots_from_compile_commands(cc_path: Path | str, *,
                                tree_root: Path | str | None = None,
                                ) -> list[Path]:
    """Every tree root a compile database implies — both of them.

    The build tree is the entries' ``directory``: every command ran there,
    and it is the tree holding generated sources such as LAPACK's ``_64``
    mirrors.  The corpus tree is the common prefix of the entries *outside*
    it.  Both are read off the database, so nothing about either tree has to
    be declared — and a declared root cannot drift from the build's.

    That the two are separable at all is the database's own doing: CMake
    compiles a generated file by its path under the build tree and a
    corpus file by its path under the source tree, recording the two
    differently in ``directory``/``file``.
    """
    from alcorpora.compile_commands import parse_compile_commands

    cmds = parse_compile_commands(Path(cc_path), tree_root=tree_root)
    if not cmds:
        return []
    build_roots = sorted({str(c.directory) for c in cmds})
    # Directories, not files: ``commonpath`` on file paths answers with a
    # path that is a prefix of those files, which for a single file is the
    # file ITSELF rather than the tree holding it.  (With many files spread
    # over several directories it happens to answer correctly, so the
    # one-file corpus is the case that exposes it.)
    outside = [os.path.dirname(str(c.file)) for c in cmds
               if not any(str(c.file).startswith(r + os.sep)
                          for r in build_roots)]
    roots = [Path(r) for r in build_roots]
    if outside:
        roots.append(Path(os.path.commonpath(outside)))
    return roots


def find_repo_root(start: Path | str) -> Path | None:
    """The version-controlled root above *start*, or ``None``.

    Asked of the FILESYSTEM rather than of git, so it works in a checkout
    without a ``.git`` directory (a tarball, a CI export) and costs one walk
    instead of a subprocess.  It exists because "is this file one the project
    SHIPS?" has to be answered against the project, and a build tree is not
    the project: every build tree holds copies of the corpus's files, which
    is what an overlay IS.
    """
    p = Path(start).resolve()
    for cand in (p, *p.parents):
        if (cand / '.git').exists() or (cand / '.github').is_dir():
            return cand
    return None


def stem_key(source: Path, dirs: dict) -> str:
    """The state/output key for *source*: ``"<dir>/<name>.<ext>"``.

    One spelling, shared by every consumer (state generation, the
    dependency index, the bisect flow).  *dirs* maps source → directory; a
    bare-stem fallback keeps callers and tests that still hand in a
    stem-keyed map working.
    """
    src = Path(source)
    sub = dirs.get(src)
    if sub is None:
        sub = dirs.get(src.stem, src.parent.name)
    return f"{sub}/{src.name}" if sub else src.name
