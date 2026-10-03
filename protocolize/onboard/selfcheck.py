"""Prove a protocol by rebuilding from it.

A protocol that has not been rebuilt from is not a product.  `problems()`
being empty says the file is well FORMED; it says nothing about whether the
build it describes can actually be run, and the two are very different
things — a protocol can be perfectly well formed and name a source nothing
produces, a link member nobody compiles, or a flag list that reaches the
compiler as one argument.

So the extractor does not get to hand over a protocol.  It hands over a
protocol that has been replayed: the pure-Fortran build is generated from it
alone, compiled, and compared against the build the project's own toolchain
made.  Nothing else in this package has that authority, and nothing outside
it does either — the proof travels WITH the artifact, because proof done
elsewhere is not proof of what was handed over.

Pure Fortran, deliberately.  The protocol was captured from a build that
compiled these units in their Fortran form, so replaying that same form is
the comparison that has a right answer.  Converting units to C++ is a
different question, asked later by the flow, against a state table this has
no business knowing about.

What is compared, and why this much
-----------------------------------
**Products, by the symbols they define.**  An archive's or an executable's
set of defined symbols is what makes it the thing it is, and it is
independent of everything that would make a byte comparison meaningless here
— object order inside an archive, timestamps, and the source path each object
embeds.  Measured: a Fortran object embeds the source path AS SPELLED ON THE
COMMAND LINE, so two builds that spell it differently (make names it
relatively, CMake absolutely) cannot produce identical objects however
correct both are.  Symbols are the honest common denominator.

**A product that does not exist is reported, not skipped.**  A protocol that
names a product nothing builds is the failure this exists to catch, and a
comparison that quietly drops it would report success for a build that is
missing a piece.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from protocolize.flat_cmake import flat_cmake
from protocolize.private_protocol import Protocol, expand_tree

__all__ = ['CheckReport', 'SelfCheckError', 'self_check']

#: Directories never mirrored into the scratch tree: build output from a
#: previous run, which the replay makes for itself, and anything the replay
#: would otherwise mistake for a source.
_SKIP = frozenset({'.git', '__pycache__', '.selfcheck'})


class SelfCheckError(RuntimeError):
    """The protocol could not be put to the test at all."""


@dataclass
class CheckReport:
    """What the replay did, and what it found.

    ``ok`` is the only judgement; everything else is evidence for it.
    """

    ok: bool = False
    units: int = 0
    products: int = 0
    #: Products found in the replayed build and compared by symbols.
    compared: int = 0
    #: Products the protocol names that the replay did not produce.
    missed: list[str] = field(default_factory=list)
    #: Products whose defined symbols differ from the project's own build.
    differing: list[str] = field(default_factory=list)
    #: Products with no counterpart in the project's own build, so nothing
    #: could be compared.  Not a failure of the protocol, but it must be
    #: visible: a check that compared nothing is not a passing check.
    uncompared: list[str] = field(default_factory=list)
    built: bool = False
    build_output: str = ''

    def summary(self) -> str:
        if not self.built:
            return (f'SELF-CHECK FAILED: the replayed build did not compile '
                    f'({self.units} units)\n{self.build_output[-4000:]}')
        lines = [
            f'self-check: {self.units} units replayed, {self.products} '
            f'products, {self.compared} compared by defined symbols',
        ]
        if self.missed:
            lines.append(f'  NOT BUILT  : {len(self.missed)} — '
                         f'{", ".join(sorted(self.missed)[:5])}')
        if self.differing:
            lines.append(f'  DIFFERING  : {len(self.differing)} — '
                         f'{", ".join(sorted(self.differing)[:5])}')
        if self.uncompared:
            lines.append(f'  UNCOMPARED : {len(self.uncompared)} — no '
                         f'counterpart in the project\'s own build')
        if self.ok:
            lines.append('  OK: the protocol rebuilt the project.')
        else:
            lines.append('  FAILED: the protocol does not rebuild the '
                         'project.')
        return '\n'.join(lines)


def _defined_symbols(artifact: Path) -> set[str]:
    """The symbols *artifact* DEFINES ITSELF.  ``nm`` is the authority.

    VERSIONED symbols are left out, and that is not tidiness.  They are the
    ones a link INHERITS rather than the ones the project defines —
    ``stdout@GLIBC_2.2.5`` is the example that was measured: whether an
    executable re-exports it depends on the linker and the C library, not on
    anything the protocol says, so keeping it makes the comparison a report
    on the linker.  Measured on MYSTRAN: 3564 common symbols and one
    difference, `stdout@GLIBC_2.2.5`; without versioned symbols, 3563 and
    none.
    """
    r = subprocess.run(['nm', '-g', '--defined-only', '--format=posix',
                        str(artifact)], capture_output=True, text=True)
    if r.returncode != 0:
        # A binary nm cannot read (stripped, or a foreign format) yields no
        # symbols rather than an exception; an EMPTY set is then reported as
        # a difference, which is the honest outcome.
        return set()
    out = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        # A MEMBER HEADER, not a symbol: `nm` on an archive prints one line
        # per member naming the archive and the member — `path/lib.a[x.o]:`.
        # Taken as a symbol it embeds the ARTIFACT'S PATH in the comparison,
        # so two archives holding the same symbols always "differ" whenever
        # they sit in different directories — which is every replay. Measured
        # on LAPACK: 334 phantom symbols on each side, all 65 products
        # reported DIFFERING while their real symbol sets were identical.
        if name.endswith(':') or '[' in name or name.endswith('.a'):
            continue
        if '@' in name:
            continue
        out.add(name)
    return out


def _has_cmake() -> bool:
    return bool(shutil.which('cmake') and shutil.which('ninja'))


def _mirror(source: Path, dest: Path) -> None:
    """A working copy of *source* at *dest*.

    A COPY, not a link farm, and not for tidiness: a replay WRITES into this
    tree — generated sources, and objects beside their sources for a build
    system that puts them there — and a hard link would carry those writes
    back into the project the protocol was captured from.
    """
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, symlinks=True,
                    ignore=shutil.ignore_patterns(*_SKIP))


def self_check(protocol: Protocol, *, tree: Path | str,
               reference_tree: Path | str, work_root: Path | str,
               jobs: int = 8) -> CheckReport:
    """Rebuild *protocol* from scratch and compare it to the real build.

    *tree* is what ``${TREE}`` means — the tree the protocol was captured
    from.  *reference_tree* is where that build's own products are, which is
    usually the same place; they are separate parameters because a project
    may build out of source and keep its artifacts in the build tree.

    *work_root* is a scratch directory this owns and may delete.
    """
    if not _has_cmake():
        raise SelfCheckError('cmake and ninja are both needed to replay')
    src_tree = Path(tree).resolve()
    ref_tree = Path(reference_tree).resolve()
    if not src_tree.is_dir():
        raise SelfCheckError(f'{src_tree} is not a directory')

    work = Path(work_root).resolve()
    scratch = work / 'tree'
    _mirror(src_tree, scratch)

    # A generated source is one the replay must MAKE before it can compile.
    # The capture tree holds the finished file — the project's build wrote
    # it — so leaving it there would let the replay compile the project's
    # output and pass without ever running the rule that produces it.
    for g in protocol.generated_inputs:
        out = Path(expand_tree(g['output'], scratch))
        if out.is_file():
            out.unlink()

    report = CheckReport(units=len(protocol.compilation),
                         products=len(protocol.products))
    # No extra declarations: a pure replay of the protocol needs no include
    # directory and no extra link — the protocol records what the build does.
    flat_cmake(protocol, tree=scratch)

    build = work / 'build'
    build.mkdir(parents=True, exist_ok=True)
    for argv in (
        ['cmake', '-S', str(scratch), '-B', str(build), '-G', 'Ninja'],
        ['cmake', '--build', str(build)],   # no -j: the generator
                                             # picks its own width
    ):
        r = subprocess.run(argv, capture_output=True, text=True)
        report.build_output = (report.build_output + '\n' +
                               f'$ {" ".join(argv)}\n' + r.stdout + r.stderr)
        if r.returncode != 0:
            return report
    report.built = True

    for product in protocol.products:
        name = Path(product.output).name
        mine = _find_artifact((build, scratch), name)
        theirs = _find_artifact((ref_tree,), name)
        if mine is None:
            report.missed.append(name)
            continue
        if theirs is None:
            report.uncompared.append(name)
            continue
        if _defined_symbols(mine) == _defined_symbols(theirs):
            report.compared += 1
        else:
            report.differing.append(name)

    report.ok = (report.built and not report.missed
                 and not report.differing and report.compared > 0)
    return report


def _find_artifact(roots: Sequence[Path], name: str) -> Path | None:
    """The first file called *name*, searching each root in turn.

    By name rather than by the protocol's recorded path, because that path
    is the PROJECT's layout and the replay is under no obligation to
    reproduce it — the flat build decides where its own artifacts go.
    """
    for root in roots:
        for p in root.rglob(name):
            if p.is_file():
                return p
    return None
