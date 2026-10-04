"""Read a protocol out of a build the corpus already has.

Onboarding a new corpus must cost as little as possible: the skill runs
once, produces a protocol, and everything after that is the flow's.  So this
reads what the BUILD SYSTEM already knows, through the build system's own
sub-tools, and never parses a manifest itself:

* ``ninja -t targets all`` — enumerate targets, and name each edge's rule
* ``ninja -t query``       — a target's inputs
* ``ninja -t commands``    — the command an edge runs
* ``ninja -t compdb``      — the compile database, as the build system
                             itself cleans it

(The archive's ``ar t`` is deliberately NOT consulted.  ``-t query`` already
names an archive's members — they are its explicit inputs — and asking two
tools for one fact is how two answers that disagree come to exist.)

Using the sub-tools rather than reading ``build.ninja`` is not a stylistic
choice.  A manifest is a build system's internal format: its escapes, its
variable expansion and its ordering rules are its own, and a parser written
here would be a second, worse implementation of something that already
exists and that the build system will keep correct.

Three of the four things below are shaped by what a CMake Fortran build
actually emits, and each one fails silently if it is guessed at:

**A unit is not a compdb entry.**  CMake compiles a scanned Fortran source
in two edges — a ``PREPROCESS`` edge whose ``file`` is the real source and
whose ``output`` is a ``<src>-pp.<ext>`` intermediate, and a ``COMPILER``
edge that consumes that intermediate.  A file loop over the database
therefore sees twice as many "sources" as there are units, half of them
files that exist only in the build tree.  The two are joined on the
intermediate, the one path both edges agree on.

**The preprocess edge carries the DEFINES.**  CMake puts a target's
``DEFINES`` property on the preprocess edge only, so a corpus can have an
empty ``-D`` set on every compile edge while the preprocessor was handed
nine.  ``preprocess`` is recorded per unit for that reason; a consumer that
re-runs only ``command`` drops every conditional block.

**A link edge's inputs are not its members.**  ``-t query`` separates the
node's own objects from what sits behind ``|``: an executable's own units
are the former and its libraries the latter, while an archive's members are
its explicit inputs.

For the small-corpus question — *why not just read the directory?* — see the
test: a source no target references is in the tree and not in the build, and
the protocol must describe the build.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from protocolize.output_layout import _owning_root
from protocolize.private_protocol import (TREE, CompileEntry, Product, Protocol,
                                  canonical_stem, escape_tree, expand_tree,
                                  normalise_build)

if TYPE_CHECKING:  # pragma: no cover
    from .selfcheck import CheckReport

__all__ = ['ExtractionError', 'Toolchain', 'extract_protocol']

#: Extensions that are a translation unit's source, by language.  The split
#: decides a unit's recorded ``language``.
FORTRAN_EXTS = frozenset({
    '.f', '.f77', '.for', '.f90', '.f95', '.f03', '.f08',
    '.F', '.F77', '.FOR', '.F90', '.F95', '.F03', '.F08',
})
CPP_EXTS = frozenset({'.c', '.cc', '.cpp', '.cxx', '.C', '.CPP'})
SOURCE_EXTS = FORTRAN_EXTS | CPP_EXTS

#: CMake's preprocessing intermediate: ``<src>-pp.<ext>``.  The extension is
#: the SOURCE's, so ``.f``, ``.f90`` and ``.F90`` all occur.  It is an
#: artifact of the dependency scan, not a translation unit.
_PP_INTERMEDIATE = re.compile(r'-pp(\.[A-Za-z][A-Za-z0-9]*)?$')

#: The object a preprocess edge feeds, as CMake spells it.
_PP_OBJ_ARG = re.compile(r'--obj=(\S+)')

#: A library a link edge names, and the rules that link.
_LIB_EXT = ('.a', '.so', '.dylib', '.lib')
_LINK_EXE = ('EXECUTABLE_LINKER',)
_LINK_LIB = ('STATIC_LIBRARY_LINKER', 'SHARED_LIBRARY_LINKER',
             'MODULE_LIBRARY_LINKER')

_MODULE_PROVIDES = re.compile(
    r'^\s*module\s+(?!procedure\b)([A-Za-z_]\w*)', re.I | re.M)
_MODULE_USES = re.compile(r'^\s*use\s+(?:,\s*\w+\s*::\s*)?([A-Za-z_]\w*)',
                          re.I | re.M)


class ExtractionError(RuntimeError):
    """The corpus's build cannot be expressed as a protocol.

    Raised with the specific gap rather than returning a protocol that looks
    complete.  An onboarding that produces a plausible-but-wrong protocol
    fails much later, in a build whose error names nothing related to the
    cause — which is the failure this module exists to prevent.
    """


# ── the toolchain ─────────────────────────────────────────────

@dataclass
class Toolchain:
    """The build system of an already-built tree, as a queryable interface.

    *root* is what :data:`protocolize.private_protocol.TREE` will DENOTE: the tree
    the protocol is meant to be replayed in.  Everything below it is
    re-spelled with the placeholder, and everything else keeps its absolute
    path — a path outside the tree names a file the replay READS rather than
    one it recreates, and rewriting it would point at nothing.
    """

    build: Path
    root: Path
    ninja: str = 'ninja'
    #: Every tree a unit's identity may be measured from.  :attr:`root` is
    #: the primary — the one the placeholder denotes — and this is the wider
    #: set when the build spans two trees (LAPACK compiles
    #: `benchmarks/lapack/**` into `build_lapack/`).  Empty means "just
    #: :attr:`root`".
    roots: tuple[Path, ...] = ()
    _targets: list[tuple[str, str]] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # ABSOLUTE, or refused.  The speller decides a path's spelling with
        # `absolute.relative_to(root)`, which against a RELATIVE root raises
        # for every path — and the speller then takes its "outside the tree"
        # branch, so the whole protocol comes out absolute and looks
        # perfectly well-formed.  Measured: 3489 generated sources and 65
        # products, none of them replayable, no error anywhere.
        for name in ('build', 'root'):
            value = getattr(self, name)
            if not Path(value).is_absolute():
                raise ExtractionError(
                    f'{name}={value!r} is relative; the tree root decides how '
                    f'every path in the protocol is spelled, and a relative '
                    f'one silently spells them ALL as absolute (a protocol '
                    f'that cannot be replayed). Pass an absolute path.')

    # ── the subtools ──
    def run(self, *args: str) -> str:
        r = subprocess.run([self.ninja, *args], cwd=str(self.build),
                           capture_output=True, text=True)
        if r.returncode:
            raise ExtractionError(
                f'`{self.ninja} {" ".join(args)}` failed in {self.build}: '
                f'{r.stderr.strip()[:400]}')
        return r.stdout

    def targets(self) -> list[tuple[str, str]]:
        """``(output, rule)`` for every edge, cached.

        ``-t targets all`` is the one subtool that enumerates what a build
        produces AND names the rule governing each, which is how the link
        products and the generated sources are found without reading the
        manifest.
        """
        if self._targets is None:
            out: list[tuple[str, str]] = []
            for line in self.run('-t', 'targets', 'all').splitlines():
                path, sep, rule = line.rpartition(': ')
                if sep and path and rule:
                    out.append((path, rule))
            self._targets = out
        return self._targets

    def rules_with(self, needle: str) -> list[str]:
        """The distinct rule names containing *needle*, sorted."""
        return sorted({r for _, r in self.targets() if needle in r})

    def compdb(self, rules: list[str]) -> list[dict]:
        """The compile database for *rules*, as the build system cleans it."""
        if not rules:
            return []
        text = self.run('-t', 'compdb', *rules)
        return json.loads(text) if text.strip() else []

    def commands_for(self, targets: list[str]) -> list[str]:
        """The command that produces each of *targets*, in order.

        ONE ninja invocation per target, run in parallel.  Not a single
        batched ``-t commands T1 .. Tn``, because that cannot be paired
        positionally: ninja prints the request's shared transitive closure
        (every edge the targets touch, dependencies AND dependents, deduped)
        interleaved with each target's own edge — measured on a real corpus,
        3489 targets produced 12243 lines with the owned commands scattered
        through them, so ``len(got) == len(targets)`` never held and nothing
        about the order was recoverable.

        For a SINGLE target the rule is exact and simple: the closure first,
        the target's OWN edge LAST.  So each is asked alone and its last line
        taken.  The fork cost is why it is threaded: ~90ms per process, so a
        few thousand targets serialize to minutes but parallelize to seconds,
        and onboarding runs once.

        The last line is also the guard: it must be the command that produces
        the target it is paired with, or the pairing is wrong and this says
        so rather than handing back a command for the wrong file.
        """
        if not targets:
            return []
        out: list[str | None] = [None] * len(targets)

        def _one(item: tuple[int, str]) -> None:
            i, t = item
            lines = [c for c in self.run('-t', 'commands', t).splitlines()
                     if c.strip()]
            if not lines or Path(t).name not in lines[-1]:
                raise ExtractionError(
                    f'ninja did not report a command producing {t}; the last '
                    f'line of its output does not name it, so the command '
                    f'cannot be paired with its output')
            out[i] = lines[-1]

        with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4))) as ex:
            # list() forces the map to run and re-raises the first failure.
            list(ex.map(_one, enumerate(targets)))
        return [c for c in out if c is not None]

    def inputs_many(self, targets: list[str]) -> list[tuple[list, list]]:
        """``(explicit, implicit)`` inputs of each target, in ONE call.

        Blocks are split first — a target line is one at column zero — and
        parsed afterwards, so the same code serves one target or all of them
        and a ``break`` cannot escape past the block it belongs to.

        ``explicit`` are the node's own sources and objects; ``implicit`` is
        everything behind a ``|`` or ``||``, which for a link is the
        libraries.  Ninja distinguishes ``|`` (implicit, still ordered) from
        ``||`` (order-only, not a rebuild trigger), but for reading a LINK
        LINE they are one thing — and both keep the order the build gave
        them, because ``ld`` is order-sensitive.
        """
        if not targets:
            return []
        text = self.run('-t', 'query', *targets)
        blocks: list[list[str]] = []
        for line in text.splitlines():
            if line and not line[0].isspace():
                blocks.append([])          # a new target's block
                continue
            if blocks:
                blocks[-1].append(line)
        if len(blocks) != len(targets):
            raise ExtractionError(
                f'asked for the inputs of {len(targets)} targets and got '
                f'{len(blocks)} blocks; the output cannot be matched to the '
                f'targets positionally')

        out: list[tuple[list, list]] = []
        for block in blocks:
            explicit: list[str] = []
            order: list[str] = []
            bucket = explicit
            for line in block:
                token = line.strip()
                if not token or token.startswith('input:'):
                    continue
                if token.startswith('outputs:'):
                    # Everything from `outputs:` on is what this node
                    # PRODUCES and what consumes it — a compile's `-pp.f`
                    # and `.ddi`.  None of it may re-run the rule, and
                    # taking it would make every generated build wait on
                    # artifacts only the ORIGINAL build system makes.
                    break
                if token.startswith(('||', '|')):
                    bucket = order
                    token = token.lstrip('|').strip()
                    if not token:
                        continue
                bucket.append(token)
            out.append((explicit, order))
        return out

    # ── path handling ──
    def to_abs(self, path: str | Path) -> Path:
        p = Path(path)
        return (p if p.is_absolute() else self.build / p).resolve()

    #: Absolute paths the BUILD produces.  Filled before the units are read,
    #: because what a path MEANS decides how it is spelled (see below).
    produced: frozenset = frozenset()

    def in_tree_space(self, path: str | Path) -> str:
        """*path* in the protocol's spelling.

        TWO CASES UNDER THE ROOT, and conflating them loses the build:

        * the path is one the build PRODUCES — a generated source, an object
          — so a replay must make it for itself and it is spelled
          ``${TREE}/<rel>``;
        * the path is one the project SHIPS and merely happens to sit under
          the build tree — NASTRAN-95's hand-written ``common_blocks.cpp``
          is in its build tree because the Makefile runs there.  That file
          does not travel with the placeholder: nothing will regenerate it,
          so ``${TREE}/common_blocks.cpp`` names a file the replay will
          never have.  Measured: the replayed build died on it.

        Anything above the root is absolute, because the replay reads it as
        it stands.

        The re-rooting escapes ``$`` (see
        :func:`protocolize.private_protocol.escape_tree`).  It has to: a build
        system's own variables and a shell's are spelled with the same
        character, and an unescaped one left in a recorded command is read
        back as a placeholder nobody wrote.
        """
        a = self.to_abs(path)
        if str(a) not in self.produced:
            try:
                a.relative_to(self.root)
            except ValueError:
                pass
            else:
                return escape_tree(str(a))
        try:
            rel = a.relative_to(self.root)
        except ValueError:
            return escape_tree(str(a))
        text = rel.as_posix()
        return TREE if text == '.' else f'{TREE}/{text}'

    def dir_spelling(self, path: str | Path) -> str:
        """A DIRECTORY's spelling: under the tree it travels, else absolute.

        A command's working directory follows the tree, which is a different
        question from :meth:`in_tree_space`'s "did the build produce this?" —
        a build directory is not produced by anything, but a replay must run
        its commands in ITS OWN copy of it, not in the tree the protocol was
        captured from.  So a directory under the root is spelled ``${TREE}``
        regardless of whether anything writes it.
        """
        a = self.to_abs(path)
        try:
            rel = a.relative_to(self.root)
        except ValueError:
            return escape_tree(str(a))
        text = rel.as_posix()
        # Escape the RELATIVE part only: `${TREE}` must stay a placeholder.
        return TREE if text == '.' else f'{TREE}/{escape_tree(text)}'

    def stem_of(self, source: Path) -> str:
        """*source*'s extension-independent identity below ITS OWN tree.

        Below the LONGEST root that contains it, not below one root: a build
        spans two trees whenever the corpus's sources live outside the build
        tree, and each file's identity must be measured from its own.  With a
        single root the out-of-tree files fall back to their basename, and a
        corpus whose testing directories share helper names (LAPACK's
        ``TESTING/EIG/alareq.f`` and ``TESTING/LIN/alareq.f``) collapses them
        into one unit — 23 stems, measured.
        """
        return _stem_of(source, self.stem_roots)

    @property
    def stem_roots(self) -> list[Path]:
        """Every tree a unit's identity may be measured from."""
        return list(self.roots) if self.roots else [self.root]

    def _spell_token(self, tok: str, directory: Path | None = None) -> str:
        """One command token, re-rooted when the build produces it.

        *directory* is where the command RUNS: a token spelled relative is
        relative to it, and this method cannot answer without knowing that.
        Leaving a relative token alone was a silent hole — the intermediate
        `CMakeFiles/t/made.f90-pp.f90` is relative in the build's own record,
        so it travelled as a relative path and the replay resolved it against
        whatever directory it happened to run in.
        """
        # Separate the flag from its operand so `-I/tree/x` keeps its `-I`.
        #
        # `@` is the same kind of case, and it cost a whole corpus: `ar qc
        # lib.a @CMakeFiles/lapack.rsp` names a RESPONSE FILE, and treating
        # `@CMakeFiles/lapack.rsp` as one path spelled it `<build>/@CMakeFiles/
        # lapack.rsp` — the prefix glued on — which does not exist anywhere.
        # The replay then archived against a file nothing had made.  The `@`
        # belongs to the program's argument syntax, not to the path, so it is
        # held back and the PATH is re-spelled on its own.
        # A RESPONSE FILE, `@<path>`, is BY DEFINITION this build's scratch:
        # every tool that has the syntax (ar, ld, the compilers) takes a file
        # the build generator wrote just before the edge ran and removed
        # after, and no build declares one as an output.  So it is re-rooted
        # by where it SITS rather than by the `produced` set, which cannot see
        # it.
        #
        # Measured on a corpus whose archive edge alone used one: the path was
        # classified as "shipped under the build tree", spelled ABSOLUTE, and
        # pointed the replay at a file that existed in neither tree.  6,794
        # units compiled and the one library failed to archive.
        if tok.startswith('@') and len(tok) > 1:
            inner = tok[1:]
            if inner.startswith('${'):
                return tok
            resolved = Path(inner)
            if not resolved.is_absolute():
                resolved = (Path(directory) / inner) if directory \
                    else Path(inner)
            resolved = resolved.resolve()
            rel = None
            try:
                rel = resolved.relative_to(Path(self.root).resolve())
            except ValueError:
                return '@' + str(resolved)          # outside: not ours
            text = rel.as_posix()
            return '@' + (TREE if text == '.' else f'{TREE}/{text}')

        for prefix in ('-I', '-isystem', '-J', '-fmoddir', '-module'):
            if tok.startswith(prefix) and len(tok) > len(prefix):
                return prefix + self._spell_token(tok[len(prefix):], directory)
        # AN OPTION IS NOT A PATH, whatever it contains.  `--tdi=CMakeFiles/
        # x.json` has a slash in its VALUE, and the relative-path branch below
        # prefixed the WHOLE token with the build directory (`<build>/--tdi=…`),
        # turning a flag into a path that does not exist — the command then
        # neither ran nor compiled, and the replay died on the unit it was
        # for.  A `-D`/`-U` operand is the same case: MYSTRAN's generators are
        # driven by `-DOUTPUT_FILE=<generated>`, so its VALUE is re-rooted when
        # it names a produced path.
        #
        # Only an ABSOLUTE produced value is re-rooted; a relative one stays
        # relative, because the command runs in the same working directory in
        # the replay as it did in the capture.
        if tok.startswith('-'):
            if '=' in tok:
                flag, _, value = tok.partition('=')
                if value.startswith('/'):
                    try:
                        resolved = Path(value).resolve()
                    except OSError:
                        return tok
                    if str(resolved) in self.produced:
                        return f'{flag}={self.in_tree_space(resolved)}'
            return tok
        # A SHELL FRAGMENT — the inner script of a nested `sh -c "..."` — is
        # not a path, so it must never be resolved or prefixed as one.  It is
        # spelled token by token instead, so the paths INSIDE it are re-rooted
        # like any other.  Measured on a real corpus: treating the fragment as
        # a relative path prefixed the whole thing with the build directory
        # (`<build>/cd <tree>/build && cmake ...`), so `cd` executed a file
        # that does not exist, the nested generator wrote nothing, and the
        # replay died on a source nobody had made.
        if any(c.isspace() for c in tok):
            try:
                sub = shlex.split(tok)
            except ValueError:
                return tok
            if len(sub) > 1:
                return _join_command([self._spell_token(s, directory)
                                      for s in sub])
            return tok
        if '/' not in tok:
            return tok
        if not tok.startswith('/') and directory is not None:
            tok = str(Path(directory) / tok)
        # A PREPROCESSED INTERMEDIATE is not in `produced` — the build writes
        # `<src>-pp.f90` as a step of its own scan, and nothing records it as
        # an output.  It is always the replay's own: a `-pp` file is the
        # build system's scratch copy of a source, no corpus SHIPS one, and
        # the two need not share a directory (CMake puts the intermediate
        # under the target's `CMakeFiles/`).  A resource is never spelled
        # this way, so the answer needs no other evidence.
        if _PP_INTERMEDIATE.search(Path(tok).name):
            a = Path(tok).resolve()
            try:
                rel = a.relative_to(Path(self.root).resolve())
            except ValueError:
                return str(a)            # outside the tree: not ours to make
            text = rel.as_posix()
            return TREE if text == '.' else f'{TREE}/{text}'
        cand = Path(tok)
        try:
            resolved = cand.resolve()
        except OSError:
            return escape_tree(tok, self.root)
        if str(resolved) in self.produced:
            return self.in_tree_space(resolved)
        # Not produced: it is READ, so it keeps naming the capture tree.
        return str(resolved)

    def in_tree_space_cmd(self, command: str,
                          directory: 'Path | None' = None) -> str:
        """*command* with the paths this build PRODUCES re-rooted.

        *directory* is where the command RUNS, and a token spelled relative is
        relative to it.  Omitted, it is the build directory — right for a
        build system that runs every command there.  A make build does not:
        each of its directories runs its own commands, so ``../mis/libmis.a``
        inside ``mds/`` names ``mds/../mis``, and resolving it against the
        build root instead left the archive spelled ABSOLUTE — the link then
        read the capture tree and a replay failed on a file it could not
        create (measured).

        TOKEN BY TOKEN, and only the produced ones.  Replacing the root as a
        substring rewrites every path that mentions it, which conflates two
        kinds of path a command carries:

        * what the build WRITES — a generated source, an object, a product.
          Those travel with the placeholder: a replay makes them again.
        * what it only READS — a header in a directory the build generates at
          CONFIGURE time, a third-party library, a resource.  Those stay
          ABSOLUTE, because they name the tree the protocol was captured from
          and that tree is the authority for everything that is not a stem.

        Rewriting the second kind is silent and expensive: MYSTRAN's
        `-I${TREE}/src_overlay/Binaries/METIS/include` sent the replay looking
        for a directory nothing recreates, and the build died on a missing
        `metis.h` — a header the capture tree had all along.

        A token the root appears inside of but that is not a whole path (a
        `-I` operand spelled relative, a shell fragment) still goes through
        the substring rewrite, because the two cases cannot be told apart
        without resolving it, and resolving loses the operands that are not
        whole paths.
        """
        try:
            argv = shlex.split(command)
        except ValueError:
            return escape_tree(command, self.root)
        out: list[str] = []
        where = Path(directory) if directory is not None else self.build
        for tok in argv:
            out.append(self._spell_token(tok, where))
        # Re-joined WITHOUT quoting the shell's own operators.  `shlex.join`
        # quotes every token it does not consider safe, and `&&` is one of
        # them — so a two-command line came back as
        #
        #     cd /tree '&&' cmake -E copy ...
        #
        # which the shell hands to `cd` as ARGUMENTS.  The command then never
        # runs, produces nothing, and the failure surfaces much later as a
        # missing file that has nothing to do with this line.  Every shipped
        # generator command is a shell line of this shape.
        return _join_command(out)


_SHELL_OPERATORS = frozenset({'&&', '||', ';', '|', '&', '>', '>>', '<',
                              '2>', '2>>', '<<'})


def _join_command(argv: list[str]) -> str:
    """*argv* as one shell line, leaving the shell's own operators alone."""
    return ' '.join(tok if tok in _SHELL_OPERATORS else shlex.quote(tok)
                    for tok in argv)


# ── the compile half ──────────────────────────────────────────

def _raw_entries(tc: Toolchain) -> list[dict]:
    """The build's own compile records, both edges of a scanned compile.

    The PREPROCESS edges are asked for whenever the build has that rule
    family, whether or not a compile database exists, and asked of the BUILD
    rather than of the database: the standard ``compile_commands.json``
    records the compile edge only, so reading defines off the database alone
    drops them.  Doing this only on the fallback path would make the
    protocol depend on whether a corpus happened to keep a database, which
    is a difference that has nothing to do with the build.
    """
    own = tc.build / 'compile_commands.json'
    rules = tc.rules_with('_COMPILER__')
    if own.is_file():
        entries = json.loads(own.read_text(encoding='utf-8'))
    elif rules:
        entries = tc.compdb(rules)
    else:
        raise ExtractionError(
            'the build has no compile rules: neither a '
            'compile_commands.json nor any `*_COMPILER__*` rule was found, '
            'so nothing here describes a translation unit')
    # A rule with no command is a phony edge that happens to share the rule
    # family (`cmake_object_order_depends_target_*` shows up this way); it
    # describes no compile and would become a unit with an empty command.
    entries = [e for e in entries if (e.get('command') or '').strip()]
    scans = tc.rules_with('PREPROCESS')
    if scans:
        entries += [e for e in tc.compdb(scans)
                    if (e.get('command') or '').strip()]
    return entries


@dataclass
class _Edges:
    """What the raw records say about one compiled object.

    Kept as one structure because the facts arrive on different edges and
    are useless apart: the object is what a link line names, the real source
    is what the unit IS, and only the preprocess edge knows the defines.
    """

    obj: Path
    real: Path | None = None
    command: str = ''
    preprocess: str = ''
    directory: str = ''


def _edges(tc: Toolchain, raw: list[dict]) -> dict[str, _Edges]:
    """``object -> its edges``, joining the compile and preprocess records.

    Joined on the INTERMEDIATE, which is the one path both edges agree on:
    the compile edge names it as ``file`` and the preprocess edge as
    ``output``.  Pairing them any other way — by stem, by basename — reads
    correctly on the simple case and mispairs the moment a corpus builds one
    source into several products, which is the common case.
    """
    out: dict[str, _Edges] = {}

    def slot(obj: str) -> _Edges | None:
        if not obj.endswith('.o'):
            return None
        key = str(tc.to_abs(obj))
        if key not in out:
            out[key] = _Edges(obj=tc.to_abs(obj))
        return out[key]

    for e in raw:
        command = (e.get('command') or '').strip()
        if not command:
            continue
        if ' -E ' in command:                      # a preprocess edge
            m = _PP_OBJ_ARG.search(command)
            got = slot(m.group(1) if m else (e.get('output') or ''))
            if got is None:
                continue
            got.preprocess = command
            # The preprocess edge's `file` IS the corpus's own source: this
            # is the edge that reads it.  (Its `output` is the intermediate.)
            src = tc.to_abs(e['file'])
            if not _PP_INTERMEDIATE.search(src.name):
                got.real = src
            continue
        got = slot(e.get('output') or '')
        if got is None:
            continue
        got.command = command
        got.directory = e.get('directory') or str(tc.build)
        src = tc.to_abs(e['file'])
        # The compile edge of a scanned source consumes the INTERMEDIATE.
        # Recording that as the unit's source would name a file no build
        # outside this build tree can compile, so it is only kept when the
        # preprocess edge never turned up to supply the real one.
        if not _PP_INTERMEDIATE.search(src.name):
            got.real = src
    return out


def _stem_roots(tc: Toolchain) -> tuple[Path, ...]:
    """Every tree this build's units belong to.

    The build tree first, then the common parent of the sources it compiles
    from OUTSIDE that tree — which is the corpus's own root.  Derived from
    the build's edge list rather than declared, for the same reason
    :func:`infer_tree_root` is: a declaration can drift from the build, and
    this one drifts silently into basename identities.

    A build whose sources are all inside it (an in-source build) gets one
    root, which is correct for it.
    """
    inside, outside = [], []
    for _, e in _edges(tc, _raw_entries(tc)).items():
        src = e.real
        if src is None:
            continue
        try:
            src.resolve().relative_to(tc.root)
        except ValueError:
            outside.append(src.resolve().parent)
        else:
            inside.append(src.resolve().parent)
    if not outside:
        return (tc.root,)
    common = os.path.commonpath([str(d) for d in outside])
    corpus = Path(common)
    return (tc.root,) if corpus == tc.root else (tc.root, corpus)


def _stem_collisions(tc: Toolchain) -> dict[str, list[str]]:
    """``{stem: [sources]}`` for every stem more than one source claims.

    A stem is a source's path BELOW the tree root, and it is what a product's
    member list and a state table's keys are written in.  Two sources that
    share one stem therefore collapse into one entry: one of them is built
    and the other is silently absent from the protocol, so a replay is
    missing a translation unit and dies at link time on a symbol nothing
    defines.

    The usual cause is a tree root one level too LOW — the sources then lie
    outside it, `stem_of` falls back to the basename, and the corpora's
    submodules collide on names as ordinary as `memory.c`, `mmd.c`, `util.c`
    (measured: MYSTRAN's metis / GKlib / superlu, 10 collisions, root set to
    the build directory instead of the tree above it).

    Reported rather than repaired: which source a stem SHOULD mean is a fact
    about the corpus, and a reader that invents an answer here produces a
    protocol that validates and cannot link.
    """
    by_stem: dict[str, list[str]] = {}
    for _, e in sorted(_edges(tc, _raw_entries(tc)).items()):
        src = e.real
        if src is None or src.suffix not in SOURCE_EXTS:
            continue
        by_stem.setdefault(tc.stem_of(src), []).append(str(src))
    # A collision is two sources of ONE LANGUAGE claiming a stem.  Two
    # LANGUAGES sharing a stem is LEGAL and occurs: a Fortran module and the C
    # function it binds (`stdlib_system.F90` + `stdlib_system.c`), linked by
    # `bind(c)`.  They are distinct translation units whose objects CMake
    # names after their sources (`.f90.o` vs `.c.o`), so they neither collide
    # nor overwrite each other.
    out: dict[str, list[str]] = {}
    for stem, sources in by_stem.items():
        by_lang: dict[str, list[str]] = {}
        for s in sorted(set(sources)):
            lang = 'fortran' if Path(s).suffix in FORTRAN_EXTS else 'cpp'
            by_lang.setdefault(lang, []).append(s)
        if any(len(v) > 1 for v in by_lang.values()):
            out[stem] = sorted(set(sources))
    return out


def _units(tc: Toolchain) -> tuple[list[CompileEntry], dict[str, str]]:
    """``(entries, object -> stem)`` for the corpus's translation units.

    Two things are returned because they answer different questions and must
    not be conflated.  The ENTRIES are the protocol's view: one per stem,
    because a stem is the protocol's key.  The MAP is the build's view: one
    entry per compiled OBJECT, because a source built into several products
    produces several objects and a link edge names whichever one it wants.

    Deriving the map from the deduped entries loses all but one of those —
    which is a protocol that validates and then cannot link.
    """
    collisions = _stem_collisions(tc)
    if collisions:
        worst = sorted(collisions.items())[:3]
        raise ExtractionError(
            f'{len(collisions)} stem(s) are claimed by more than one source, '
            f'so a unit would be silently dropped:\n'
            + '\n'.join(f'  {k}: {", ".join(v)}' for k, v in worst)
            + f'\nThis usually means the tree root is one level too LOW — '
              f'the sources lie outside {tc.root}, so their identity falls '
              f'back to the basename. Pass the tree above it.')
    out: list[CompileEntry] = []
    by_object: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    for key, e in sorted(_edges(tc, _raw_entries(tc)).items()):
        src = e.real
        if src is None or src.suffix not in SOURCE_EXTS:
            continue                # an intermediate with no recoverable source
        stem = tc.stem_of(src)
        language = 'fortran' if src.suffix in FORTRAN_EXTS else 'cpp'
        by_object[key] = stem
        if (stem, language) in seen:
            # The same (stem, language) twice is ONE translation unit compiled
            # into several products: LAPACK builds 72 of TESTING's helpers
            # into up to eight executables each.  One entry describes it.
            continue
        seen.add((stem, language))
        provides, uses = _module_edges(src)
        # Paths first, then the tree placeholder: absolutising uses the
        # capture directory, which the placeholder would have erased.
        cmd = _absolutise_paths(e.command, e.directory)
        pp = _absolutise_paths(e.preprocess, e.directory) if e.preprocess else ''
        out.append(CompileEntry(
            directory=tc.dir_spelling(e.directory),
            command=tc.in_tree_space_cmd(cmd),
            file=tc.in_tree_space(src),
            output=_rel(e.obj, tc.build),
            stem=stem,
            provides=provides,
            uses=uses,
            language=language,
            preprocess=(tc.in_tree_space_cmd(pp)
                        if language == 'fortran' else ''),
        ))
    if not out:
        raise ExtractionError(
            'no translation units were found in the compile database')
    return out, by_object


def _stem_of(src: Path, roots: 'Path | list[Path]') -> str:
    """The unit's identity below the tree it lives in.

    Relative to that tree, not to the build directory: the same source can be
    referenced from several places and its identity must not depend on which
    one asked.  The extension comes off because ``isamax.f`` and
    ``isamax.cpp`` are one unit and one link member.

    *roots* may be several, and the LONGEST one containing *src* wins.  That
    is not tidiness: a build spans two trees whenever the corpus's sources
    sit outside the build tree, and measuring them from the build tree makes
    every one of them fall back to its basename — LAPACK then collides on 23
    names it shares between ``TESTING/EIG`` and ``TESTING/LIN``, and 23 units
    are silently dropped.
    """
    candidates = [roots] if isinstance(roots, Path) else list(roots)
    src = Path(src).resolve()
    owning = _owning_root(src, [Path(r) for r in candidates])
    if owning is not None:
        rel = src.relative_to(owning)
    else:
        rel = Path(src.name)
    return canonical_stem(rel.as_posix())


def _rel(path: Path, base: Path) -> str:
    """*path* relative to *base* where it can be, else absolute.

    Objects are spelled relative to the build directory the way the build
    system itself spells them, so the pair ``(directory, output)`` resolves
    exactly as it did in the original build.
    """
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


#: Path-taking flags whose operand may be spelled RELATIVE to the directory
#: the build ran in.  The compiler resolves such an operand against its own
#: CWD, which is that directory — a fact the recorded command does not carry
#: and a replay does not share.
_RELATIVE_PATH_FLAGS = ('-J', '-I', '-module', '-isystem')


def _absolutise_paths(command: str, directory: str) -> str:
    """Re-spell a command's relative path operands, against *directory*.

    A recorded command is only meaningful in the directory it was recorded
    in: ``-JSRC`` names ``<capture>/SRC``, and a build generated elsewhere
    resolves it against a directory that has nothing to do with the project.
    The failure is a missing ``.mod`` — a module file written to one place
    and looked for in another — which names neither the flag nor the
    directory that is actually wrong.

    Left as a relative path it is not merely incorrect, it is incorrect in
    a way that LOOKS self-consistent: every unit in the replay shares one
    working directory, so they all agree with each other and disagree only
    with the project.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return command
    if len(argv) < 2:
        return command
    base = Path(directory)
    out = [argv[0]]
    i = 1
    while i < len(argv):
        tok = argv[i]
        for flag in _RELATIVE_PATH_FLAGS:
            if tok == flag and i + 1 < len(argv):
                operand = argv[i + 1]
                if not operand.startswith(('${', '/')):
                    operand = str((base / operand).resolve())
                out += [tok, operand]
                i += 2
                break
            if (len(tok) > len(flag) and tok.startswith(flag)
                    and not tok[len(flag):].startswith(('${', '/'))):
                out.append(flag + str((base / tok[len(flag):]).resolve()))
                i += 1
                break
        else:
            out.append(tok)
            i += 1
    return shlex.join(out)


def _module_edges(src: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The modules *src* defines and uses, AS THE SOURCE SPELLS THEM.

    Case is KEPT: how a module name is spelled is the language profile's
    business, not this tool's — lowercasing here would be this repo knowing a
    Fortran rule.  Matching two spellings is a separate question, done
    case-insensitively where the graph is built (see flat_cmake), because a
    language that is case-insensitive to its compiler must not have a graph
    that misses on case.

    Read from the source text, because the build system does not record the
    module graph and a build system needs it to order a compile.
    """
    try:
        text = src.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return (), ()
    provides = tuple(dict.fromkeys(m.group(1)
                                   for m in _MODULE_PROVIDES.finditer(text)))
    uses = tuple(dict.fromkeys(m.group(1)
                               for m in _MODULE_USES.finditer(text)))
    return provides, uses


# ── the link half ─────────────────────────────────────────────

def _link_targets(tc: Toolchain) -> list[tuple[str, str, bool]]:
    """``(target, kind, shared)`` for every link edge, deterministically ordered.

    The KIND and the SHAREDNESS come from the rule, not from inspecting the
    command: a build system already knows whether it is making an executable,
    a static library or a shared one, and a command sniffing for ``ar`` in
    ``argv[0]`` reads that off a convention rather than off the declaration.
    Sharedness matters because it changes what a CONSUMER defines — see
    :attr:`private_protocol.Product.shared`.
    """
    out: list[tuple[str, str, bool]] = []
    for path, rule in tc.targets():
        if any(k in rule for k in _LINK_EXE):
            out.append((path, 'exe', False))
        elif ('SHARED_LIBRARY_LINKER' in rule
              or 'MODULE_LIBRARY_LINKER' in rule):
            out.append((path, 'archive', True))
        elif any(k in rule for k in _LINK_LIB):
            out.append((path, 'archive', False))
    return sorted(set(out))


def _products(tc: Toolchain, by_object: dict[str, str]) -> list[Product]:
    """The link products, with their members recorded as STEMS.

    Members are stems, never file names: a member spelled ``isamax.f.o``
    pins the link line to one form, and a unit converted to C++ would then
    drop out of the archive while the link line still asked for it.

    The inputs come from ``-t query``, which is the build system's own
    answer about its own graph, rather than from the link command's operands
    — a command may name a response file, or a library by a path that is not
    an input edge at all.
    """
    targets = _link_targets(tc)
    inputs = tc.inputs_many([t for t, _, _ in targets])
    # The EXACT link command, per target, so a replay runs it verbatim rather
    # than re-deriving a link line (see Product.command).  Asked of the same
    # subtool and the same way the generated sources' commands are.
    commands = tc.commands_for([t for t, _, _ in targets])
    out: list[Product] = []
    for (target, kind, shared), (explicit, order), command in zip(
            targets, inputs, commands):
        objects: list[str] = []
        libs: list[str] = []
        for tok in (*explicit, *order):
            stem = by_object.get(str(tc.to_abs(tok)))
            if stem is not None:
                objects.append(stem)
                continue
            if Path(tok).suffix in _LIB_EXT:
                libs.append(tc.in_tree_space(tok))
        objects = list(dict.fromkeys(objects))
        libs = list(dict.fromkeys(libs))
        if not objects:
            # A link edge with no unit of ours in it — a helper executable
            # the build system makes for itself (CMake probes the compiler
            # this way).  It is not part of the corpus's protocol.
            continue
        out.append(Product(
            output=tc.in_tree_space(target),
            kind=kind,
            shared=shared,
            # What goes INTO the product, by stem — one field for both
            # kinds, because `kind` already says which reading applies.
            # `libs` is separate because a library is a PATH, not a stem:
            # it may be one this protocol does not build.
            stems=tuple(objects),
            libs=tuple(libs) if kind == 'exe' else (),
            # Re-rooted like every other recorded command, so a replay writes
            # its products into ITS tree, not the capture tree.
            command=tc.in_tree_space_cmd(
                _absolutise_paths(command, str(tc.build))) if command else '',
            directory=tc.dir_spelling(tc.build),
            # The response files THIS command reads, by the path it spells.
            # Read off the recorded command rather than the build's bindings,
            # because the question is which of them this edge actually uses —
            # and a path only this product names is exactly the one a replay
            # would otherwise look for in the capture tree.
            # Spelled as the command spells it — relative to `directory`, the
            # same convention `output` uses — so a replay materialises it
            # where the command will look.
            rspfiles=tuple(
                shlex.split(cmd_tok)[0][1:]
                for cmd_tok in (command.split() if command else ())
                if cmd_tok.startswith('@') and len(cmd_tok) > 1),
        ))
    return out


# ── the generated half ────────────────────────────────────────

def _generated(tc: Toolchain) -> list[dict]:
    """Sources the build produces, with the command and inputs that make them.

    ``CUSTOM_COMMAND`` is the rule a build system gives a hand-written
    generator, and the outputs that are SOURCES are the ones a generated
    build must reproduce: it cannot compile a file that does not exist.

    Deduped by ABSOLUTE path rather than by target name, and not just for
    tidiness: a generator names each output twice, once relative to the tree
    and once absolutely, as two separate targets.  Asking for a command per
    SPELLING would ask twice for one file, which is the misalignment the
    count check in :meth:`Toolchain.commands_for` exists to refuse.
    """
    pairs: dict[str, str] = {}
    for path, rule in tc.targets():
        if rule != 'CUSTOM_COMMAND':
            continue
        if Path(path).suffix not in SOURCE_EXTS:
            continue
        pairs.setdefault(str(tc.to_abs(path)), path)
    if not pairs:
        return []
    ordered = sorted(pairs.items())
    # Asked by the spelling `targets()` reported, not by the resolved path: a
    # target has exactly the name its build system gave it, and asking for
    # the absolute form fails on any generator that did not declare one.  The
    # resolved path is for identity, not for asking.
    spelled = [sp for _, sp in ordered]
    commands = tc.commands_for(spelled)
    inputs = tc.inputs_many(spelled)

    out: list[dict] = []
    for (abs_out, _), command, (explicit, order) in zip(ordered, commands,
                                                        inputs):
        # The rule's inputs are what must re-run it, deduped because the two
        # spellings of one edge give its inputs twice.
        depends = sorted({tc.in_tree_space(tc.to_abs(t))
                          for t in (*explicit, *order)})
        out.append({
            'output': tc.in_tree_space(abs_out),
            'command': tc.in_tree_space_cmd(command),
            'depends': depends,
        })
    return out


# ── the whole thing ───────────────────────────────────────────


def _cmake_cache(build: Path) -> Path | None:
    """The CMake cache of *build*, if it is a CMake tree."""
    direct = build / 'CMakeCache.txt'
    if direct.is_file():
        return direct
    for sub in sorted(build.glob('*/CMakeCache.txt')):
        return sub
    return None


#: Directory-making flags, and which `build` field each operand belongs to.
_DIR_FLAGS: dict[str, str] = {'-I': 'include_dirs', '-isystem': 'include_dirs',
                              '-J': 'module_dirs', '-module': 'module_dirs'}


def _dir_operands(command: str) -> list[tuple[str, str]]:
    """Every ``(flag, directory)`` a command carries, in either spelling.

    ``-Jdir`` and ``-J dir`` are one fact told two ways, and a reader that
    knew only one would miss half the directories on a real corpus.  Only
    flags this module knows the meaning of are returned.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return []
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        hit = None
        for flag in _DIR_FLAGS:
            if tok == flag and i + 1 < len(argv):
                out.append((flag, argv[i + 1]))
                i += 2
                hit = flag
                break
            if len(tok) > len(flag) and tok.startswith(flag):
                out.append((flag, tok[len(flag):]))
                i += 1
                hit = flag
                break
        if hit is None:
            i += 1
    return out


def capture_build(proto: Protocol, tree_root: Path | str, *,
                  cfg: dict | None = None) -> dict:
    """The ``build`` section: observed where it can be, declared where not.

    Two of the three kinds of fact here need no declaration at all, because
    the build already states them.  The module and include directories are
    the operands of ``-J``/``-I`` on the recorded commands -- so they are read
    from *proto* rather than asked of a corpus setup script a second time: a
    value stated twice drifts, and the drift is silent.  A directory under
    `${TREE}` travels with the protocol; one outside it is the project's own
    and stays absolute, like every other such path.

    The third kind cannot be observed, and that is why it is a DECLARATION:
    ``policy`` flags (``-falign-functions=64``) are applied by the consumer to
    every unit and appear on no recorded command, so a protocol that fails to
    carry them builds without them and says nothing.  ``optimize`` is the same
    shape -- a mapping from the source's optimization token to the one the
    CONVERTED code must use -- and a corpus that needs none says so with an
    empty map rather than by omission.

    *cfg* is the corpus config the keys have lived in; they are read from it
    here so that the section can be filled for a corpus onboarded before the
    keys moved into the protocol, and left empty for one that declares none.
    """
    cfg = cfg or {}
    tree = Path(tree_root).resolve()
    mods: list[str] = []
    incs: list[str] = []
    for e in proto.compilation:
        for flag, operand in _dir_operands(e.command or ''):
            bucket = mods if _DIR_FLAGS[flag] == 'module_dirs' else incs
            bucket.append(_tree_spelling(Path(operand), tree))
    flags = cfg.get('flags') if isinstance(cfg.get('flags'), dict) else {}
    optimize = flags.get('optimize') or cfg.get('optimize') or {}
    policy = flags.get('policy') or cfg.get('policy_flags') or []
    return normalise_build({
        'flags': {'optimize': dict(optimize), 'policy': list(policy)},
        'module_dirs': sorted(set(mods)),
        'include_dirs': sorted(set(incs)),
    })


def capture_prepare(project_root: Path | str | None,
                    *, declared: 'list[dict] | None' = None) -> list[dict]:
    """The ``prepare`` section: what the corpus needs ONCE, checked each time.

    The project states the biggest part of this itself.  A ``.gitmodules``
    entry IS the declaration "this directory is a library I vendored", and it
    comes with the fetch command git maintains for it — so a submodule op is
    DERIVED from the project rather than listed by hand.  Its ``check`` is the
    submodule's own path, which is exactly the corpus's way of telling that
    the fetch already happened, so a second onboarding pays nothing.

    The rest cannot be derived from the tree, because it is not in the tree:
    a portability patch the corpus needs to run at all is a Delta between the
    shipped source and a buildable one, and nothing in the shipped source
    states it.  Those arrive as *declared* ops (the skill fills them), and
    are carried through with their checks intact rather than re-derived.

    Order is preserved: `declared` first, then the submodules — a patch that
    has to land before a submodule is fetched keeps its place, and the
    derived ops only ever append.
    """
    ops: list[dict] = [dict(op) for op in (declared or ())]
    if project_root:
        for path in gitmodules_paths(project_root):
            ops.append({
                'op': 'submodule',
                'check': {'path': path},
                'run': f'git submodule update --init {path}',
            })
    return ops


def gitmodules_paths(project_root: Path | str) -> list[str]:
    """The submodule paths the PROJECT declares, via git's own reader.

    ``.gitmodules`` is where a project states "this directory is a library I
    vendored", and ``git config -f`` is the interface git maintains for
    reading it — the file's escapes and continuation rules are git's, not
    ours, so it is asked rather than parsed.

    Asked of the project checkout, which the onboarding run has: it is the
    project it just built, and it passed its directory to cmake/make.  The
    BUILD tree does not carry this file (MYSTRAN's build copies only
    ``Source/`` and ``submodules/`` into its overlay), which is why the
    build's own cache is the fallback rather than the other way round.
    """
    import subprocess
    root = Path(project_root)
    if not (root / '.gitmodules').is_file():
        return []
    r = subprocess.run(
        ['git', 'config', '-f', str(root / '.gitmodules'),
         '--get-regexp', r'^submodule\..*\.path$'],
        capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        return []
    out = []
    for line in r.stdout.splitlines():
        _, _, val = line.partition(' ')
        if val.strip():
            out.append(val.strip().strip('/'))
    return out

def subproject_roots(build: Path | str,
                     tree_root: Path | str) -> tuple[str, ...]:
    """The source dirs the build pulled in as SUB-PROJECTS, in stem space.

    A vendored library is a project the corpus called in and built, and a
    CMake build says so in its own cache: ``CMAKE_HOME_DIRECTORY`` is the
    project, and every other ``<Name>_SOURCE_DIR`` is a directory a
    ``project()`` was declared in and ``add_subdirectory`` pulled in.

    Measured, this is exactly the split and nothing else is:

    * MYSTRAN — ``Mystran_SOURCE_DIR`` IS ``CMAKE_HOME_DIRECTORY``, beside
      ``GKlib``/``LAPACK``/``METIS``/``SuperLU``.  The four are vendored.
    * LAPACK — one entry, ``LAPACK_SOURCE_DIR``, the home directory itself.
      Nothing is vendored, so all 6794 units are LAPACK's own.
    * NASTRAN-95 — a Makefile build with no cache, so no sub-projects.

    Every signal that was tried before this one and rejected on the real
    corpora, each for a reason the data shows:

    * **"compiled into an archive"** — ``liblapack.a``'s 4373 members ARE
      LAPACK's own code, so it marks the one corpus that IS a library as
      foreign.
    * **the exe vs archive member sets** — disjoint for LAPACK, but they
      intersect for MYSTRAN (the vendored SuperLU host code is in both).
    * **object output paths, include flags, ``add_subdirectory`` in the
      sources** — MYSTRAN compiles its own ``Source/`` AND its vendored
      ``submodules/`` into the same ``CMakeFiles/mystran.dir/`` with the same
      ``-J`` module directory; and LAPACK's own ``INSTALL/`` declares a
      ``project()`` too, so "a nested project" alone is not provenance.
    * **``.gitmodules``** — the right idea, but the build tree does not have
      it: MYSTRAN's build copies only ``Source/`` and ``submodules/`` into
      its overlay, and the protocol is captured from the BUILD tree, so
      provenance has to be read from what the build itself recorded.

    The result is returned as the corpus's OWN roots — the complement —
    because that is what a consumer scopes TO.  A root is a prefix holding
    one of the corpus's own units and no vendored one.  Empty means no
    narrowing: every unit is the corpus's.
    """
    build = Path(build).resolve()
    cache = _cmake_cache(build)
    if cache is None:
        return ()
    values: dict[str, str] = {}
    for line in cache.read_text(encoding='utf-8', errors='replace').splitlines():
        if '=' not in line or line.startswith(('//', '#')):
            continue
        key, _, val = line.partition('=')
        values[key.split(':')[0]] = val.strip()

    def _norm(text: str) -> str:
        return str(Path(text).resolve())

    home = values.get('CMAKE_HOME_DIRECTORY', '')
    subdirs = {
        _norm(v) for k, v in values.items()
        if k.endswith('_SOURCE_DIR') and v and _norm(v) != _norm(home)
    } if home else set()
    if not subdirs:
        return ()
    return tuple(sorted(subdirs))


def own_roots_for(proto: Protocol, build: Path | str,
                  tree_root: Path | str,
                  project_root: Path | str | None = None) -> tuple[str, ...]:
    """``proto``'s own roots — the complement of what the project VENDORED.

    Two statements of the same fact, preferred in this order because they are
    the same fact stated by the same party at different distances:

    1. **``.gitmodules``**, read through ``git config -f``.  The project
       says outright which directories are libraries it vendored.  This is
       the authority, and the onboarding run can always read it: it is the
       run that BUILT the project, so it knows the checkout it built.
    2. **the build's CMake cache**, whose ``<Name>_SOURCE_DIR`` entries name
       every directory a ``project()`` was declared in that the top-level
       build pulled in.  The same answer, recorded by the build, for a run
       that captured a tree without its checkout beside it.

    Whichever states it, the vendored set is then closed under generation to
    a fixed point: a unit made FROM a vendored unit is the vendored library's
    too.  That closure is what a path test on a corpus root cannot see, and
    it cuts both ways — MYSTRAN's 570 ``*_64.f`` are made from
    ``submodules/lapack`` and go, while LAPACK's 3489 are made from LAPACK's
    OWN ``BLAS/SRC`` and stay.

    **Nothing here mentions a suffix.** A unit is kept iff it participates in
    the build as the corpus's own, and the ``*_64`` units are just units: the
    ones LAPACK generated are LAPACK's, the ones MYSTRAN generated are the
    library's, and the rule says so without knowing what a suffix is.

    Signals that were tried and REJECTED, each on the real corpora:

    * **"compiled into an archive"** — ``liblapack.a``'s 4373 members ARE
      LAPACK's own code, so it marks the one corpus that IS a library as
      foreign.
    * **the exe vs archive member sets** — disjoint for LAPACK, but they
      intersect for MYSTRAN (the vendored SuperLU host code is in both).
    * **object output paths, include flags, ``add_subdirectory`` in the
      sources** — MYSTRAN compiles its own ``Source/`` AND its vendored
      ``submodules/`` into the same ``CMakeFiles/mystran.dir/`` with the same
      ``-J`` module directory.
    * **a nested ``project()`` alone** — LAPACK's own ``INSTALL/`` declares
      one too, so it is only usable together with the cache's record of what
      the TOP-LEVEL build actually pulled in.

    Empty means no narrowing: every unit is the corpus's.  A root is the
    shortest prefix of an own unit that holds no vendored one.
    """
    vendored = _vendored_stems(proto, build, tree_root, project_root)
    if not vendored:
        return ()
    own = [e.stem for e in proto.compilation if e.stem not in vendored]
    if not own:
        return ()
    roots: set[str] = set()
    for path in own:
        parts = path.split('/')
        for i in range(1, len(parts) + 1):
            pref = '/'.join(parts[:i])
            # Too shallow: an ancestor OF a vendored unit, so it holds the
            # library too.  One level deeper.
            if any(v == pref or v.startswith(pref + '/') for v in vendored):
                continue
            roots.add(pref)
            break
    return tuple(sorted(roots))


def _vendored_stems(proto: Protocol, build: Path | str,
                    tree_root: Path | str,
                    project_root: Path | str | None) -> set[str]:
    """The units that belong to a library the project vendored."""
    vendored: set[str] = set()

    declared = gitmodules_paths(project_root) if project_root else []
    if declared:
        # Project-relative paths, matched as whole path segments because the
        # protocol spells the same directory through the build's overlay
        # (`submodules/lapack` becomes `src_overlay/submodules/lapack/...`).
        for e in proto.compilation:
            seg = f'/{e.stem}/'
            if any(f'/{d}/' in seg for d in declared):
                vendored.add(e.stem)
    else:
        tree = Path(tree_root).resolve()
        for d in subproject_roots(build, tree_root):
            try:
                rel = Path(d).relative_to(tree).as_posix()
            except ValueError:
                continue          # outside the tree: not ours to map
            if rel:
                vendored.update(
                    e.stem for e in proto.compilation
                    if e.stem == rel or e.stem.startswith(rel + '/'))

    if not vendored:
        return set()
    by_file = {e.file: e.stem for e in proto.compilation}

    def _stem(text: str) -> str:
        if text.startswith(TREE):
            return canonical_stem(text[len(TREE):].lstrip('/'))
        return by_file.get(text) or canonical_stem(text)

    order = list(proto.generated_inputs or ())
    for _ in range(len(order) + 1):
        changed = False
        for g in order:
            out = _stem(str(g.get('output') or ''))
            if out in vendored:
                continue
            ins = [_stem(str(d)) for d in (g.get('depends') or ())]
            if ins and any(i in vendored for i in ins):
                vendored.add(out)
                changed = True
        if not changed:
            break
    return vendored



def capture_tests(build: Path | str, tree_root: Path | str) -> list[dict]:
    """The build's test registrations, through CTest's own JSON interface.

    A generated build must be runnable the way the project runs it, and for a
    self-verifying corpus the test suite IS the verdict — so the
    registrations have to travel with the protocol.  They are read from
    ``ctest --show-only=json-v1``, the interface CTest maintains for exactly
    this, rather than from the generated ``CTestTestfile.cmake``: that is
    CMake's internal format, and its escapes, variable expansion and ordering
    belong to CMake.

    Each entry keeps only what a generator needs to re-register it — the
    name, the command as an argument vector, the working directory and the
    ``DEPENDS`` list (LAPACK's summary test depends on all 214 drivers).  The
    backtrace is dropped: it names the project's own CMakeLists line, which a
    generated build does not have.

    Paths are re-spelled against *tree_root* the same way every other path in
    the protocol is, so a replay registers tests against ITS tree rather than
    the one the protocol was captured from.  A build with no CTest
    registration returns ``[]`` — an absent declaration, not an error.
    """
    build = Path(build).resolve()
    ctest = shutil.which('ctest')
    if ctest is None:
        return []
    if not (build / 'CTestTestfile.cmake').is_file():
        return []
    try:
        proc = subprocess.run([ctest, '--show-only=json-v1'], cwd=str(build),
                              capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode:
        return []
    text = proc.stdout
    start = text.find('{')
    if start < 0:
        return []
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[start:])
    except (ValueError, json.JSONDecodeError):
        return []

    tree = Path(tree_root).resolve() if tree_root else build

    def _spell(value: str) -> str:
        return escape_tree(str(value), tree)

    out: list[dict] = []
    for t in payload.get('tests') or ():
        name = str(t.get('name') or '')
        command = [_spell(str(c)) for c in (t.get('command') or ())]
        if not name or not command:
            continue
        entry: dict = {'name': name, 'command': command}
        props = t.get('properties') or ()
        for prop in props:
            pname = str(prop.get('name') or '')
            if pname == 'WORKING_DIRECTORY':
                entry['working_directory'] = _spell(str(prop.get('value')))
            elif pname == 'DEPENDS':
                # CTest reports this one as a LIST already.  Stringifying it
                # and splitting on ';' wrapped the whole list in a
                # one-element list of its own repr, which a generator would
                # then register as a single dependency on a nonsense name.
                value = prop.get('value')
                if isinstance(value, str):
                    deps = [d for d in value.split(';') if d]
                else:
                    deps = [str(d) for d in (value or ())]
                if deps:
                    entry['depends'] = deps
        out.append(entry)
    return out



def capture_support_files(tests: list[dict], build: Path | str,
                          tree_root: Path | str) -> list[dict]:
    """Files the registered tests READ that neither compile nor generate.

    A test command names three kinds of path, and they must not be treated
    alike:

    * ``-DOUTPUT=`` — where the driver WRITES its results.  A file, not an
      input; capturing it would hand the suite a run it did not perform.
    * an executable under the tree — a build product, produced by the build
      like any other.
    * anything else the command READS — LAPACK's ``lapack_testing.py`` (the
      aggregator the project's CMake ``file(COPY)``s in) and its eight
      ``*_64.in`` inputs, which CMake derives from the corpus's own ``.in``
      by substituting output names.

    The last kind is what this captures, as CONTENT: there is no command to
    replay, because the derivation is a ``string(REPLACE)`` in the project's
    own CMakeLists, and reimplementing it here would be a second and worse
    implementation of something CMake already ran.

    Measured on LAPACK: 9 files, ~101 KB.  The registry's own inputs, not a
    copy of the project.
    """
    build = Path(build).resolve()
    tree = Path(tree_root).resolve() if tree_root else build
    seen: dict[str, dict] = {}

    def _add(src: Path, dest_rel: str) -> None:
        if dest_rel in seen or not src.is_file():
            return
        try:
            data = src.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return
        seen[dest_rel] = {'path': dest_rel, 'content': data}

    for t in tests:
        command = list(t.get('command') or ())
        for arg in command:
            if arg.startswith('-DOUTPUT=') or arg.startswith('/usr/') \
                    or Path(arg).name in ('cmake', 'ctest'):
                continue
            value = arg.split('=', 1)[1] if arg.startswith('-D') \
                and '=' in arg else arg
            if value.startswith(TREE):
                src = Path(expand_tree(value, tree))
                try:
                    rel = src.relative_to(tree).as_posix()
                except ValueError:
                    continue
                if src.suffix == '' and src.is_file() and os.access(
                        src, os.X_OK):
                    continue          # a build product, not a support file
                _add(src, rel)
            elif not value.startswith('-') and '/' not in value \
                    and value.endswith('.py'):
                # A bare relative name: the command runs in its working
                # directory, so the file sits in the tree root.
                _add(build / value, value)
    return [seen[k] for k in sorted(seen)]


def extract_protocol(build_dir: Path | str, *,
                     tree_root: Path | str | None = None,
                     comment: str = '',
                     work_root: Path | str | None = None,
                     verify: bool = True,
                     project_root: Path | str | None = None,
                     prepare: 'list[dict] | None' = None,
                     cfg: dict | None = None) -> Protocol:
    """Read a whole protocol out of *build_dir* and PROVE it, whoever built it.

    The proof is not optional and it is not a separate step a caller can
    forget: a protocol that has not been rebuilt from is not a product, so
    this REFUSES to return one.  `problems()` empty means the file is well
    formed, which says nothing about whether the build it describes can be
    run — and the failures that matter here are exactly the ones that are
    well formed and unrunnable.

    *work_root* is where the proof's scratch trees go; it defaults to a
    sibling of the build tree.  *verify=False* skips the proof and exists
    only for iterating on the extractor itself, never for handing a protocol
    to anything.

    The backend is DECIDED here rather than passed in, and decided from the
    TREE rather than from a flag: a tree holding ``build.ninja`` or
    ``CMakeCache.txt`` is a ninja build, a tree holding Makefiles is a make
    build, and a tree holding neither is refused with that said plainly.
    A flag would move the question to the caller, and every caller that
    guessed wrong would get an error naming the tool it did not use — which
    is exactly how this used to fail for a make project.

    *tree_root* is what :data:`protocolize.private_protocol.TREE` will mean — the
    tree the sources live in, which is what makes the result movable.  It
    defaults to what each backend's own layout implies.

    *prepare* is the corpus's declared one-time infrastructure that the tree
    cannot state (a portability patch); the submodule ops are derived from
    *project_root*'s own ``.gitmodules`` and appended.

    *cfg* supplies the two DECLARATIONS the `build` section carries and no
    recorded command does: the policy flags applied to every unit and the
    optimization level the converted code is built at.  They are passed in
    rather than re-derived because they are corpus policy, and a protocol
    that dropped them would build without them silently.
    """
    build = Path(build_dir).resolve()
    proto = _read(build, tree_root, comment, project_root)
    # RECORD where this came from, as observed.  `${TREE}` is the BUILD tree
    # (see :func:`infer_tree_root`), and whether the build ran IN the source
    # tree or beside it changes where every generated source lands — so a
    # consumer must not re-derive it, and a caller must not override it with
    # a guess (a `--tree-root` one level off silently re-roots thousands of
    # generated sources).  The skill fills `capture`; the flow reads it.
    tree = Path(tree_root).resolve() if tree_root else infer_tree_root(build)
    proto.capture = {
        'tree': str(tree),
        'layout': 'in_source' if tree == build else 'out_of_source',
    }
    # The two facts a compile database cannot carry, filled HERE rather than
    # in the ninja reader: both are properties of the protocol, not of the
    # backend that happened to produce it, so a make tree gets them too.
    proto.build = capture_build(proto, tree, cfg=cfg)
    proto.prepare = capture_prepare(project_root, declared=prepare)
    if not verify:
        return proto
    _prove(proto, build, tree_root, work_root)
    return proto


def _read(build: Path, tree_root: Path | str | None,
          comment: str,
          project_root: Path | str | None = None) -> Protocol:
    """The protocol, from whichever way this tree can be read.

    Two shapes are read here and they are the same shape: ASK THE BUILD
    SYSTEM what it would run.  A ninja tree answers through its own
    sub-tools; a tree whose build is a Makefile answers through `make -Bn`.
    Neither is "a backend" in the sense of a plugin written in advance --
    both are the one question, asked of whichever tool the tree offers.
    """
    if _is_ninja_tree(build):
        return _read_ninja(build, tree_root, comment, project_root)
    from .make_extract import extract_make_protocol, make_directories
    if make_directories(build):
        return extract_make_protocol(
            build, tree_root=tree_root or build, comment=comment)
    raise ExtractionError(
        f'{build} does not answer "what would you run?" in any way this '
        f'knows: no build.ninja, no CMakeCache.txt, and no Makefile in it or '
        f'in an immediate subdirectory.  Ask the project how it is built, '
        f'build it, and keep the tree it left behind.  Do NOT infer compile '
        f'commands by reading the sources: a guessed protocol is one whose '
        f'flags are almost right, which is the most expensive kind of wrong.')


def _read_ninja(build: Path, tree_root: Path | str | None,
                comment: str,
                project_root: Path | str | None = None) -> Protocol:
    """The protocol of a ninja or CMake build tree."""
    root = (Path(tree_root).resolve() if tree_root
            else infer_tree_root(build))
    tc = Toolchain(build=build, root=root)
    # The SECOND tree, when the build has one.  A build spans two trees
    # whenever the corpus's sources live outside the build tree, and each
    # file's identity must be measured from its own: measuring LAPACK's
    # sources from `build_lapack/` drops every one of them to its basename,
    # and the 23 names TESTING/EIG and TESTING/LIN share collapse into 23
    # units.  Asked of the build — the sources it names ARE the answer — so
    # nothing declares it and nothing can drift from it.
    tc.roots = _stem_roots(tc)
    # What the build PRODUCES must be known BEFORE anything is spelled: a
    # path's meaning decides its spelling, and the speller asks `produced`.
    #
    # This set therefore cannot be built FROM `_generated`, which is itself a
    # client of the speller — measured on the real LAPACK tree, that circular
    # ordering emitted all 3489 generated sources and all 65 products as
    # ABSOLUTE paths under the capture tree.  A replay from such a protocol
    # writes 3489 files into the project it was supposed to be proving
    # something about, which is the one thing a self-check must never do.
    #
    # Both sides come from the build's own edge list instead: `_edges` gives
    # every output of every rule (objects, intermediates, generated sources,
    # link targets), and the generated SOURCES are the edges whose rule is
    # the build system's hand-written-generator rule.
    produced: set[str] = {str(k) for k in _edges(tc, _raw_entries(tc))}
    for path, rule in tc.targets():
        # By RULE FAMILY, not by "ends in .o": the set has to hold every
        # path this build WRITES, and the ones that are easy to forget are
        # the very ones a replay must not aim at the capture tree — the
        # generated sources and the link products.  (An `-o *.o` filter gave
        # the compile objects and silently nothing else: measured, all 3489
        # generated sources and all 65 products came out absolute.)
        if (rule == 'CUSTOM_COMMAND' and Path(path).suffix in SOURCE_EXTS):
            produced.add(str(tc.to_abs(path)))
        elif any(k in rule for k in (*_LINK_EXE, *_LINK_LIB)):
            produced.add(str(tc.to_abs(path)))
    tc.produced = frozenset(produced)
    generated = _generated(tc)
    entries, by_object = _units(tc)
    proto = Protocol(
        compilation=entries,
        products=_products(tc, by_object),
        generated_inputs=generated,
        comment=comment or f'onboarded from {build}',
    )
    # WHICH units are the corpus's own is a fact about the project, and the
    # project states it: the build's cache names every sub-project it pulled
    # in.  Recorded here so that every consumer reads ONE answer instead of
    # re-deriving one from a config field; see :func:`own_roots_for`.
    proto.own_roots = own_roots_for(proto, build, root, project_root)
    proto.tests = capture_tests(build, root)
    proto.support_files = capture_support_files(proto.tests, build, root)
    return proto


def extract_and_prove(build_dir: Path | str, *,
                      tree_root: Path | str | None = None,
                      comment: str = '',
                      work_root: Path | str | None = None,
                      jobs: int = 8,
                      project_root: Path | str | None = None,
                      ) -> tuple[Protocol, 'CheckReport']:
    """Extract, prove, and hand back BOTH the protocol and the evidence.

    :func:`extract_protocol` is this with the report thrown away; it exists
    for a caller that only wants the artifact and the refusal.  A caller that
    has to SHOW someone the proof — which the onboarding skill does, because
    a protocol nobody has seen rebuilt is a protocol nobody should trust —
    needs the report itself.
    """
    build = Path(build_dir).resolve()
    proto = _read(build, tree_root, comment, project_root)
    work = (Path(work_root).resolve() if work_root
            else build.parent / '.selfcheck')
    report = _prove(proto, build, tree_root, work, jobs=jobs)
    return proto, report


def _prove(proto: Protocol, build: Path, tree_root: Path | str | None,
           work_root: Path | str | None, *, jobs: int = 8) -> 'CheckReport':
    """Rebuild from *proto* and refuse it if the rebuild does not match.

    Refusing is the whole point.  A protocol is what every later build is
    generated from, so a wrong one is not a wrong answer — it is a wrong
    answer multiplied by every build that follows, each failing somewhere
    that names the project rather than the protocol.
    """
    from .selfcheck import self_check
    # The SAME answer the extraction used, from the same function.  A
    # different one here compares the replay against the wrong tree, and the
    # failure reads as "the protocol does not rebuild the project" when the
    # build was fine.  `build.parent` was that wrong answer for an
    # out-of-source CMake build whose tree root is the build directory
    # itself: it pointed at the enclosing directory, where the products are
    # NOT.
    root = (Path(tree_root).resolve() if tree_root
            else infer_tree_root(Path(build)))
    work = (Path(work_root).resolve() if work_root
            else build.parent / '.selfcheck')
    report = self_check(proto, tree=root, reference_tree=root,
                        work_root=work, jobs=jobs)
    if not report.ok:
        raise ExtractionError(
            f'the protocol was extracted but did NOT rebuild the project, '
            f'so it is not usable:\n{report.summary()}')
    return report


def infer_tree_root(build: Path) -> Path:
    """What ``${TREE}`` should denote for an out-of-source CMake build.

    Asked of the BUILD rather than left to the caller, because the caller has
    no way to know and a wrong answer is silent: a placeholder one level off
    names ``<root>/<root>/SRC/...``, and the extraction still reports
    thousands of units with no problems.

    The answer is the BUILD TREE, and the reason is what a build tree is
    for.  A project's sources lie where the project keeps them — often
    outside the build tree, sometimes in a separate checkout — and the
    protocol leaves those ABSOLUTE, because they do not move and a replay
    must read the very same files.  What the build tree holds, and what a
    replay has to make for itself, is what the build GENERATED: LAPACK's
    build tree contains 3,489 `_64` mirrors that exist nowhere else.

    So the placeholder names the build tree, and the entries that live there
    travel with the protocol; the entries that do not, do not.  In-source
    builds make no difference here — their sources are inside the build tree
    and are re-rooted, which is correct for them because a replay of an
    in-source project is expected to be one.

    WHEN THE BUILD HAS NO SOURCES OF ITS OWN, the answer moves UP.  MYSTRAN
    is built as ``<tree>/_build`` while every source it compiles lives in
    ``<tree>/src_overlay``; rooted at ``_build`` the sources are all OUTSIDE
    the tree, so their identity falls back to the basename and the
    submodules collide on names as ordinary as ``memory.c``, ``mmd.c`` and
    ``util.c``.  Measured: 10 stems, 3349 units instead of 3339.  A tree
    whose only source below it is a generated file names a subtree, not a
    tree, and the answer is the directory above it.
    """
    build = Path(build).resolve()
    # Only a NINJA build can be asked this way — the collision test reads the
    # build's edge list through ninja's subtools.  A make tree is asked by its
    # own backend (see `make_extract`), and running ninja at it would be a
    # command that fails on a tree that is perfectly readable.
    if not _is_ninja_tree(build):
        return build
    # Ask the question the root actually answers: do the units keep a
    # DISTINCT identity below it?  A root one level too low puts the sources
    # outside, their identity falls back to the basename, and submodules
    # collide — so the test is the collisions themselves, not "are there
    # sources here".  (MYSTRAN's `_build` DOES hold sources: the `_64`
    # mirrors it generated.  A "has sources" test therefore says the root is
    # fine while 10 stems are being silently merged.)
    if not _stem_collisions(Toolchain(build=build, root=build)):
        return build
    parent = build.parent
    if parent != build and parent.is_dir():
        up = Toolchain(build=build, root=parent)
        if not _stem_collisions(up):
            return parent
    return build


def _is_ninja_tree(path: Path) -> bool:
    """Does *path* hold a ninja build?  Asked of the tree, not of a flag."""
    return (path / 'build.ninja').is_file() or (path / 'CMakeCache.txt').is_file()


# ── the acceptance declaration ────────────────────────────────

#: The order the shapes are tried, most explicit first.  A build system's own
#: registry is the project's clearest statement, so it outranks a file the
#: corpus maintains by hand.
_CASE_LIST_NAMES = ('cases.txt', 'cases.files', 'testcases.txt')
_CASE_DIR_NAMES = ('verification', 'Verification', 'tests/cases')


def capture_verification(proto: Protocol, build: Path | str,
                         tree_root: Path | str,
                         project_root: Path | str | None = None,
                         declaration: Path | str | None = None,
                         facts: Path | str | dict | None = None) -> dict:
    """How this project declares that a run of it was CORRECT.

    The build half of onboarding asks the build system what it would do; this
    asks the project how it says a run is right.  Three answers are known,
    and they are told apart by what the project HAS, never by its name:

    * **the build system registered tests** (``proto.tests`` is non-empty) —
      the most explicit declaration there is, and already captured.  A suite
      the build system runs is self-verifying: nothing to store.
    * **a case list** — records in a file, with an ``INCLUDE`` chain and
      per-record fields.  The record layout is READ FROM THE FILE rather than
      assumed: the project's own columns.
    * **one directory per case** — a fixed-named input inside each.  The
      whole directory is staged, because a case's supporting decks and its
      restart state sit beside it.

    A project that declares none of these is REFUSED.  An empty plan is not a
    neutral answer: every case it would fail to find is a case that cannot
    fail, so the refusal is the only honest outcome.

    *declaration* names the file where the corpus states its comparison
    rules — which lines are volatile, and how much numeric slack its physics
    allows.  It is passed IN rather than searched for, because guessing at it
    is how a capture reads another corpus's rules: in this repo those scripts
    live with the tool rather than beside the corpus, so a search rooted at
    the project finds nothing and the honest answer is the caller's.

    Returns the plan as JSON-ready data; :class:`protocolize.verification_plan` is
    what reads it.
    """
    build = Path(build).resolve()
    root = Path(tree_root).resolve()
    proj = Path(project_root).resolve() if project_root else build.parent

    rules = _corpus_rules(declaration)
    if isinstance(facts, (str, Path)):
        facts = json.loads(Path(facts).read_text(encoding='utf-8'))
    if proto.tests:
        return _verification_from_registry(proto, build, root, rules)
    found = _locate(proj, (_find_case_lists, _find_case_dirs))
    if found is not None:
        if found.is_dir():
            return _verification_from_case_dirs(proto, found, build, root,
                                                rules, proj, facts)
        return _verification_from_case_list(proto, found, build, root,
                                            rules, facts)
    raise ValueError(
        f'{proj} declares no way to verify a run: no registered tests, no '
        f'case list ({" / ".join(_CASE_LIST_NAMES)}), and no per-case '
        f'directory ({" / ".join(_CASE_DIR_NAMES)}).  An empty plan is not a '
        f'neutral answer — every case it fails to find is a case that cannot '
        f'fail — so this is refused rather than returned.')


def _tree_spelling(path: Path, root: Path) -> str:
    """*path* spelled against ``${TREE}`` when it is under it, else absolute.

    The protocol's own two spellings, for the protocol's own reason: a path
    under the tree travels with the capture; one outside it does not move.
    """
    path = Path(path).resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        return str(path)
    return f'${{TREE}}/{rel}' if str(rel) != '.' else '${TREE}'


def _rule_sections(rules: dict | None) -> list[str]:
    """The report's sections, as the corpus's own reader names them.

    Taken from the corpus's declaration rather than typed here: this module
    may not name a corpus's sections any more than it may name a corpus.
    """
    got = (rules or {}).get('sections')
    return list(got) if got else []


def _verification_from_registry(proto: Protocol, build: Path,
                                root: Path,
                                rules: dict | None = None) -> dict:
    """A suite the build system runs: a tree, self-verifying, no golden."""
    # The width the corpus measured at, when it declared one.  It is a corpus
    # fact, not a harness constant: the drivers are CPU-bound, so a run at a
    # different width is a different measurement.  Bound into argv only when
    # declared — a plan that bound `{jobs}` without a value would measure
    # whatever the machine happens to have, and `problems()` refuses exactly
    # that.
    suite_jobs = int((rules or {}).get('suite_jobs') or 0)
    argv = ['ctest']
    if suite_jobs:
        argv += ['-j', '{jobs}']
    argv += ['-V', '--timeout', '60']
    run: dict = {'kind': 'suite', 'argv': argv, 'cwd': '{target}',
                 'exit_accept': [0], 'case_timeout_s': 60}
    if suite_jobs:
        run['jobs'] = suite_jobs
    # The assertions this shape can honestly make, from what the project
    # DECLARED — never a fixed list.  A project whose suite prints its own
    # section table declares `section_pattern`; one whose runner is the build
    # system's own (`ctest`) prints the runner summary and nothing else.
    # Emitting the table assertion unconditionally was measured to FAIL a
    # plain ctest project: no section table exists, so the reader found
    # nothing and the verdict read "a report that could not be read".
    assertions: list[dict] = []
    if (rules or {}).get('section_pattern'):
        # The project's own table counts individual numerical CHECKS; the
        # runner summary counts whole TESTS.  Neither subsumes the other, so
        # both are read where both exist.  `require_min: {run: <floor>}` is
        # the project's own magnitude and the conjunct that makes the others
        # mean anything.
        assertions.append(
            {'kind': 'self_report', 'in': 'suite_stdout',
             'section_pattern': rules['section_pattern'],
             # The row that carries the section's TOTAL — the project's own
             # word.  Not enumerated in code: which label is the total, like
             # which sections exist, is the project's to say.
             'total_label': rules.get('total_label', ''),
             'require_min': {'run': 1},
             'require_zero': ['numerr', 'otherr']})
    # The runner's own summary, for any suite whose runner prints it.  This is
    # the build system's spelling (`ctest`), not a project's.
    assertions.append(
        {'kind': 'self_report', 'in': 'suite_stdout',
         'reader': {'pattern':
                    r'(?P<pct>[\d.]+)%\s+tests passed,\s+'
                    r'(?P<failed>\d+)\s+tests failed out of\s+'
                    r'(?P<total>\d+)'},
         'require_min': {'total': 1}, 'require_zero': ['failed']})
    return {
        'plan_version': 1,
        'target': {'kind': 'tree'},
        'cases': {'kind': 'build_system_tests', 'tests': 'protocol.tests'},
        'inputs': {},
        'staging': [],
        'run': run,
        'produced': ([{'name': 'TESTING/**/*.out', 'as': 'driver_logs'}]
                     if (rules or {}).get('section_pattern') else []),
        'golden': {'source': 'none', 'root': '{origin}', 'path': '',
                   'stamp': 'self_verifying.origin'},
        'assertions': assertions,
        'report': {'repeats': 1},
        'summary': {'require_zero': ['diff', 'crash', 'timeout'],
                    'require_min': {'pass': 1}},
        'provenance': ('the reference build itself reported a clean run; '
                       'there is no stored golden, so this judges stability '
                       'against the corpus\'s own self-check tables'),
        'comment': f'captured from {build}',
    }


def _tiers(proj: Path) -> list[list[Path]]:
    """Where a project's acceptance declaration may live, in precedence order.

    Tier 0 is the project itself; tier 1 is the siblings that are ITS OWN.  A
    validation suite maintained as its own repository is a sibling, not a
    subdirectory — measured on MYSTRAN, whose decks live in
    ``MYSTRAN_Validation`` beside ``MYSTRAN`` — so a search rooted only at the
    project refuses a project that is in fact fully equipped.

    A sibling counts as this project's when it is NAMED AFTER IT, which is how
    such a suite is named in practice.  The association is not a detail:
    searching every sibling instead measured a NASTRAN plan built from
    MYSTRAN's case list, because the parent directory is shared by every
    corpus in this repo.  That is exactly the failure this mechanism exists to
    prevent, and it was reproduced by the code meant to prevent it.

    Tiers rather than one flat search because the project's own declaration
    must outrank a neighbour's, and ambiguity must be judged WITHIN a tier: a
    short-circuit per search root would take the alphabetically first of two
    equally valid candidates without saying so.
    """
    tiers = [[proj]]
    parent = proj.parent
    if parent == proj:
        return tiers
    stems = {proj.name, proj.name.replace('-', '_'), proj.name.replace('_', '-')}
    siblings = [s for s in sorted(parent.iterdir())
                if s != proj and s.is_dir()
                and any(s.name.startswith(stem) for stem in stems)]
    if siblings:
        tiers.append(siblings)
    return tiers


def _find_case_lists(root: Path) -> list[Path]:
    """Every case list *root* declares, in the project's own file names.

    Our own scratch is filtered out.  `.work` holds a MIRROR OF THE WHOLE
    TREE, so an unfiltered `rglob` finds the project's declaration twice and
    `_locate` — correctly — refuses as ambiguous.  The refusal is right; the
    file that provoked it is ours, not the project's.
    """
    out: list[Path] = []
    for name in _CASE_LIST_NAMES:
        for cand in sorted(root.glob(f'**/{name}')):
            if cand.is_file() and cand not in out:
                out.append(cand)
    return _our_own_files(out)


def _find_case_dirs(root: Path) -> list[Path]:
    """Every directory under *root* shaped like one-subdirectory-per-case."""
    out: list[Path] = []
    for name in _CASE_DIR_NAMES:
        for cand in sorted(root.glob(f'**/{name}')):
            if cand.is_dir() and any(d.is_dir() for d in cand.iterdir()) \
                    and cand not in out:
                out.append(cand)
    return _our_own_files(out, dirs=True)


def _locate(proj: Path, finders) -> Path | None:
    """The one declaration the project means, or a refusal naming the many.

    Ambiguity is REFUSED rather than resolved by picking one: picking
    silently is how a corpus comes to be verified against another project's
    cases, and the failure then looks like a wrong answer instead of a wrong
    choice.
    """
    for tier in _tiers(proj):
        found: list[Path] = []
        for root in tier:
            for finder in finders:
                for cand in finder(root):
                    if cand not in found:
                        found.append(cand)
        if not found:
            continue
        if len(found) > 1:
            names = ', '.join(str(f) for f in found[:5])
            raise ValueError(
                f'ambiguous: {len(found)} acceptance declarations are '
                f'reachable from {proj} ({names}).  Which one is this '
                f"project's is not something to guess — picking silently "
                f"verifies the corpus against another project's cases and "
                f'the result reads as a wrong answer rather than a wrong '
                f'choice.')
        return found[0]
    return None


def _target_dirs(proto: Protocol) -> list[str]:
    """The directories a product corpus's binaries were BUILT into, as bindings.

    Taken from where the products' own commands wrote them, not assumed: a
    CMake project puts its executable under `<tree>/build`, a Makefile project
    where it linked.  `{tree}` is the binding for `${TREE}` — the RUN says
    which tree that is.
    """
    dirs: list[str] = []
    for p in proto.products:
        if p.kind != 'exe':
            continue
        d = str(Path(p.output).parent).replace('${TREE}', '{tree}')
        if d not in dirs:
            dirs.append(d)
    return dirs or ['{tree}/bin']


def _record_layout(sample: str) -> dict:
    """The record's own columns, read from a line of it.

    Read rather than assumed: ``MYSTRAN``'s list is ``type; deck; selector;
    operation; reference; tolerance`` and a corpus that gains a column must
    be a plan change, not a parser change.
    """
    fields = [f.strip() for f in sample.split(';')]
    n = len(fields)
    layout = {'sep': ';', 'comment': '#',
              'types': {'field': 0, 'accept': ['mys', 'msc', 'pth']},
              'deck': {'field': 1}, 'group_by': 'deck'}
    if n >= 3:
        layout['selector'] = {'field': 2}
    if n >= 4:
        layout['operation'] = {'field': 3}
    if n >= 5:
        layout['expected'] = {'field': 4}
    if n >= 6:
        layout['tolerance'] = {'field': 5}
    return layout


def _chain_records(cases_file: Path) -> list[str]:
    """Every record a case list declares, THROUGH its INCLUDE chain.

    Not the first record-looking line of the top-level file: a corpus whose
    list is a table of contents — which is what MYSTRAN's ``cases.txt`` is —
    has no records in it at all, only ``INCLUDE`` lines.  Reading a layout
    from that file yields one empty field and a plan with no columns, and the
    absence is silent: the fields simply are not there.
    """
    seen: set[Path] = set()
    out: list[str] = []

    def _walk(path: Path) -> None:
        if path in seen or not path.is_file():
            return
        seen.add(path)
        for raw in path.read_text(encoding='utf-8',
                                  errors='replace').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.upper().startswith('INCLUDE'):
                parts = line.split()
                if len(parts) >= 2:
                    _walk(path.parent / parts[1])
                continue
            out.append(line)

    _walk(cases_file)
    return out


def _widest_record(cases_file: Path) -> str:
    """The record that shows the most columns — the corpus's real layout.

    Not the first record: a corpus's list mixes asserted and unasserted
    entries, and an unasserted one simply omits its trailing fields (MYSTRAN
    writes ``mys; deck;`` for a deck judged only by running).  The layout is
    the WIDEST shape the corpus uses; reading it off a narrow record would
    drop the per-quantity criteria for the whole corpus, which is exactly the
    information the plan exists to carry.
    """
    records = _chain_records(cases_file)
    if not records:
        return ''
    return max(records, key=lambda r: len(r.split(';')))



#: What a corpus's benchmark script calls its volatile-line list and its
#: tolerance.  Read by NAME because a name is what a module constant has; the
#: shapes below are the two the corpora actually use.
def _corpus_rules(declaration: Path | str | None) -> dict:
    """The corpus's own comparison rules, READ from the file that states them.

    Which lines are volatile cannot be inferred from the outputs: a date
    differs from a date, and at the corpus's own tolerance a one-day
    difference sits inside the numeric slack, so the comparison would call it
    a match.  So it is read from the corpus's own declaration — the place the
    corpus states it — exactly as ``capture_tests`` asks ``ctest`` rather
    than guessing at a registration.

    It is a file of VALUES, and that is the point.  The rules used to live
    inside the corpus's benchmark script, which this imported, so the corpus
    could not be onboarded without that script and the script could not be
    deleted while it was the only place the rules existed.  A declaration
    written as code is refused by name rather than executed: a mechanism
    that runs a corpus's Python to learn a number is the mechanism this
    replaces.

    A corpus that declares none gets none — the result is empty rather than
    filled in.  Inventing a pattern list would drop lines the corpus never
    said were volatile, which is a comparison that accepts a difference.
    """
    found: dict = {}
    if declaration is None:
        return found
    path = Path(declaration)
    if not path.is_file():
        return found
    text = path.read_text(encoding='utf-8')
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f'{path} is not a data declaration ({exc}).  A corpus states its '
            f'comparison rules as VALUES — a JSON object with a "comparison" '
            f'key — never as code for the capture to execute.') from exc
    spec = raw.get('comparison') or {}
    if isinstance(spec.get('drop'), list):
        found['drop'] = [str(p) for p in spec['drop']]
    # `keep` (a whitelist) and `mask` (in-line trimming) are the other two
    # shapes of "what not to compare", and they are read here for the same
    # reason `drop` is: they are the corpus's own statement, not something to
    # infer from its output.  `mask` is the one that matters for a line that
    # mixes a judged value with a run-dependent one — dropping such a line
    # stops comparing the judged value too, which is a comparison that
    # accepts a difference.
    if isinstance(spec.get('keep'), list):
        found['keep'] = [str(p) for p in spec['keep']]
    if isinstance(spec.get('mask'), list):
        pairs: list = []
        for m in spec['mask']:
            if isinstance(m, (list, tuple)) and len(m) == 2:
                pairs.append([str(m[0]), '' if m[1] is None else str(m[1])])
        if pairs:
            found['mask'] = pairs
    for key in ('rtol', 'atol'):
        if isinstance(spec.get(key), (int, float)):
            found[key] = float(spec[key])
    if spec.get('section_pattern'):
        found['section_pattern'] = str(spec['section_pattern'])
    if spec.get('total_label'):
        found['total_label'] = str(spec['total_label'])
    # The width a suite is measured at, read from the declaration's TOP level
    # rather than under ``comparison``: it is a measurement parameter, not a
    # way of comparing two runs.  It must be captured here or the capture's
    # own reader (`_verification_from_registry`) sees it as 0 — the width was
    # once declared and silently read as absent, so the suite ran at a width
    # nobody chose.
    if isinstance(raw.get('suite_jobs'), int) and not isinstance(
            raw['suite_jobs'], bool):
        found['suite_jobs'] = int(raw['suite_jobs'])
    return found


def _lockstep_rules(rules: dict | None) -> dict:
    """A ``text_lockstep`` assertion's parameters, as the corpus declares them."""
    rules = rules or {}
    out = {'drop': rules.get('drop', []),
           'rtol': rules.get('rtol', 0.05),
           'atol': rules.get('atol', 0.5)}
    # Only carry the modes the corpus DECLARED.  An absent `keep`/`mask` must
    # stay absent rather than become an empty list: the plan schema refuses a
    # key the mechanism does not read, and an empty list would be noise the
    # reader has to reason about.  A declared one travels.
    if rules.get('keep'):
        out['keep'] = list(rules['keep'])
    if rules.get('mask'):
        out['mask'] = [list(p) for p in rules['mask']]
    return out


def _deck_root(cases_file: Path, decks: list[str]) -> Path:
    """The directory the case list's deck paths are relative to.

    NOT necessarily the directory the list itself is in: measured on MYSTRAN,
    ``cases.txt`` sits in ``MYSTRAN_Validation/`` and the decks it names live
    in ``MYSTRAN_Validation/decks/``, so a plan that resolved them against the
    list's own directory looked for 485 files that are not there.

    Derived from the tree rather than assumed, and rather than read from the
    corpus's Python: the answer is whichever directory actually holds the
    decks, which is a fact about the project.
    """
    base = cases_file.parent
    if not decks:
        return base
    best, best_n = base, 0
    for cand in [base] + sorted(d for d in base.iterdir() if d.is_dir()):
        n = sum(1 for rel in decks[:40] if (cand / rel).is_file())
        if n > best_n:
            best, best_n = cand, n
    return best


def _verification_from_case_list(proto: Protocol, cases_file: Path,
                                 build: Path, root: Path,
                                 rules: dict | None = None,
                                 facts: dict | None = None) -> dict:
    """A case list: per-deck runs, each judged against the reference's output.

    The paths MIRROR the deck's own relative path, which is what the corpus
    does (``out_rel = deck_rel.with_suffix('.out')``): a deck named
    ``Benchmark/bar_01.DAT`` produces ``Benchmark/bar_01.out``.  Measured
    without that: a plan looking for ``<stem>.out`` found none of the
    reference's 973 golden files, because they keep the deck's subdirectory.
    """
    sample = _widest_record(cases_file)
    decks = _case_decks(cases_file, sample)
    proj_root = cases_file.parent
    decks_root = _deck_root(cases_file, decks)
    product = ''
    for p in proto.products:
        if p.kind == 'exe':
            product = Path(p.output).name
            break
    return {
        'plan_version': 1,
        'target': {'kind': 'product', 'product': product,
                   'dirs': _target_dirs(proto)},
        'cases': {'kind': 'case_list',
                  'root': _tree_spelling(proj_root, root),
                  'deck_root': _tree_spelling(decks_root, root),
                  'files': [cases_file.name],
                  'include': {'directive': 'INCLUDE', 'resolve': 'relative'},
                  'record': _record_layout(sample)},
        'inputs': {'from': 'input_only'},
        'staging': [],
        'run': {'kind': 'process',
                'argv': ['{target}', '{input.name}'], 'cwd': '{work}',
                'exit_accept': [0], 'case_timeout_s': 60},
        # The artifact keeps the deck's relative path, and is named what the
        # program actually WRITES.  The golden is named what the reference
        # harness SAVED, which is a different suffix on this corpus: a plan
        # that assumed the two agree found no artifact and reported "no
        # output" for all 485 cases.
        'produced': [{'name': _artifact_spelling(facts), 'as': 'out'}],
        'golden': {'source': 'reference_run', 'root': '{origin}',
                   'path': _golden_spelling(facts)},
        'assertions': _case_list_assertions(facts, rules),
        'report': {'repeats': 1},
        'summary': {'require_zero': ['diff', 'crash', 'timeout'],
                    'require_min': {'pass': 1}},
        'provenance': ('the golden is the reference build run through this '
                       'same plan, so an agreement proves stability; the '
                       'corpus also ships per-quantity criteria, which are '
                       'declared in cases.record and are its only external '
                       'truth'),
        'comment': f'captured from {cases_file}',
    }


def _case_list_assertions(facts: dict | None, rules: dict | None) -> list:
    """How a case-list corpus's output is judged.

    A corpus whose output is TABLES is judged by its column model: which
    token positions of each table are physical quantities.  A corpus with no
    such model is judged line by line.  The model is the corpus's own — it is
    read from the declaration, never inferred from the output, because which
    column is DERIVED and which is physical is a fact about the physics.
    """
    model = (facts or {}).get('field_model')
    if model:
        return [
            {'kind': 'artifact', 'path': '{out}', 'exists': True},
            {'kind': 'field_assertion', 'produced': '{out}',
             'reference': '{golden}', 'model': model,
             **_lockstep_rules(rules)},
        ]
    return [
        {'kind': 'artifact', 'path': '{out}', 'exists': True},
        {'kind': 'text_lockstep', 'produced': '{out}',
         'reference': '{golden}', **_lockstep_rules(rules)},
    ]


def _artifact_spelling(facts: dict | None) -> str:
    """Where this corpus's program WRITES its report, per deck.

    ``{deck_out}`` is the deck's relative path with the suffix the corpus
    writes; a corpus whose program writes something else says so in its own
    facts.  The default is the deck's own suffix, which is what a program
    that writes next to its input does.
    """
    suffix = ((facts or {}).get('artifact') or {}).get('suffix')
    # Written into the case's own work dir, named after the input it was
    # given — which is where a program run with `cwd={work}` writes.
    return '{deck_out}' if not suffix else '{work}/{input.stem}' + suffix


def _golden_spelling(facts: dict | None) -> str:
    """What the reference harness SAVED a golden as, per deck.

    Kept separate from the artifact's name because on this corpus they are
    different suffixes for the same bytes, and only the corpus knows that.
    """
    suffix = ((facts or {}).get('artifact') or {}).get('golden_suffix')
    # Mirrors the DECK's own relative path, under the origin — which is where
    # the reference harness saved it (`out_rel = deck_rel.with_suffix('.out')`).
    return '{deck_out}' if not suffix else '{deck_stem}' + suffix


def _case_decks(cases_file: Path, sample: str) -> list[str]:
    """The deck paths the case list names, through its INCLUDE chain.

    Read with the record layout so the right column is taken: the deck is a
    named field, not "the second thing on the line".
    """
    rec = _record_layout(sample)
    sep = rec.get('sep', ';')
    deck_field = int((rec.get('deck') or {}).get('field', 1))
    accept = set((rec.get('types') or {}).get('accept') or ())
    type_field = int((rec.get('types') or {}).get('field', 0))
    out: list[str] = []
    for line in _chain_records(cases_file):
        fields = [f.strip() for f in line.split(sep)]
        if len(fields) <= max(deck_field, type_field):
            continue
        if accept and fields[type_field] not in accept:
            continue
        deck = fields[deck_field].replace('\\', '/')
        if deck and deck not in out:
            out.append(deck)
    return out






def _chains_from(case_dir: Path, facts: dict | None) -> dict:
    """The corpus's restart chains, read from the catalogue it is given.

    A restart chain is the one acceptance fact this repo cannot extract from
    its corpus.  Four candidate derivations were tried and MEASURED against
    the corpus's own tree, and none reproduces the table:

    * id succession — needs two different groupings (``D01-01-1A/B/C``
      increments the letter, ``D02-02-1A..7A`` the number), and one case's
      chain is deliberately NOT its immediate predecessor: ``T03-12-1C``'s
      own input says "INSERT T03121A (NOT T03121B) CHECKPOINT DICTIONARY".
    * shipped restart state — a directory holding ``.sof1`` or ``.optp`` is
      one that restarts, but ``D02-03-1A`` ships state without being in the
      table.  The marker answers "does this case declare a dependency", not
      "what is it".
    * the corpus's prose — only 5 of the 19 are named in its text.
    * ``verification/Archive/inp/`` membership — true of all 132 directories.
      No information.

    So it travels as CATALOGUED DATA, and the catalogue is passed IN for the
    same reason *declaration* is: only the caller knows which corpus it is
    onboarding, and a search for one would find another's.  A corpus with no
    catalogue declares no chains — which is the honest answer, not a
    fallback — and the plan says whose data it is via `provenance`.

    An entry naming a case this tree does not have is REFUSED rather than
    dropped: a dropped chain is a case judged without the state it needs.
    """
    raw = (facts or {}).get('chains') or {}
    table = raw.get('order') or {}
    if not table:
        return {}
    chains = {k: list(v) for k, v in table.items()
              if (case_dir / k).is_dir()
              and all((case_dir / d).is_dir() for d in v)}
    if len(chains) != len(table):
        raise ValueError(
            f'the restart catalogue names cases this corpus does not have: '
            f'{sorted(set(table) - set(chains))}.  A chain that '
            f'cannot be formed is a case that will be judged on state that '
            f'was never built.')
    return {'order': chains, **({'carry': raw['carry']} if raw.get('carry') else {}),
            'provenance': raw.get('_comment', 'catalogued by hand')}


def _staging_facts(facts: dict | None) -> dict:
    """The corpus's own description of what its programs need in order to RUN.

    Read from the corpus's declaration rather than typed here, for the reason
    the whole capture exists: the names of a corpus's environment variables,
    its program config, and the text inside that config are facts about THAT
    corpus.  Measured without them, a program exits 0 and writes nothing, and
    every case reports "no output" — which reads as a broken transpiler.

    A corpus that declares none gets none: an empty value is a plan that
    stages nothing, which is the honest answer rather than a fallback.
    """
    return (facts or {}).get('preparation') or {}


def _staging_for(proj: Path, case_dir: Path, root: Path,
                 facts: dict | None = None) -> list[dict]:
    """The preparation ops the corpus's declaration calls for.

    The SEARCH is generic — the corpus's own tree says where the resources
    are — while the NAMES are the corpus's.  A declaration that names a
    resource the tree does not have is refused rather than skipped: a missing
    resource is a program that cannot run, and a run that cannot start must
    not report a difference as if it had.
    """
    spec = _staging_facts(facts)
    if not spec:
        return []
    ops: list[dict] = []
    home = spec.get('home_env')
    if home:
        ops.append({'op': 'env', 'name': home, 'value': '{home}'})
    resource = spec.get('resource_dir')
    if resource:
        found = _our_own_files(proj.rglob(resource), dirs=True)
        if not found:
            raise ValueError(
                f'the corpus declares a resource directory {resource!r} that '
                f'its tree does not have under {proj} — a program that looks '
                f'for it cannot run.')
        ops.append({'op': 'symlink', 'link': '{home}/' + resource,
                    'target': str(found[0].resolve())})
    scratch = spec.get('scratch_subdir')
    if scratch:
        # Named per case, so two cases cannot share one program's
        # intermediate state.
        ops.append({'op': 'mkdir', 'path': '{home}/{case_id}/' + scratch})
    config_env = spec.get('config_env')
    configs = []
    if config_env or spec.get('substitutions'):
        glob = spec.get('config_glob')
        if not glob:
            raise ValueError(
                'the corpus declares a config environment variable or a '
                'substitution but no config_glob, so there is no file to '
                'find it in')
        configs = _our_own_files(proj.rglob(glob))
        if not configs:
            raise ValueError(
                f'the corpus declares a config file {glob!r}, which its tree '
                f'does not have under {proj}')
    if config_env:
        ops.append({'op': 'env', 'name': config_env,
                    'value': str(configs[0].resolve())})
    if spec.get('substitutions'):
        ops.append({'op': 'substitute', 'from': str(configs[0]),
                    'to': '{work}/' + configs[0].name, 'required': True,
                    'replace': spec['substitutions']})
    for name, value in sorted((spec.get('env') or {}).items()):
        ops.append({'op': 'env', 'name': name, 'value': str(value)})
    if spec.get('alias_upper'):
        ops.append({'op': 'alias', 'in': '{work}'})
    return ops


def _our_own_files(paths, *, dirs: bool = False) -> list[Path]:
    """A project search's hits, MINUS what this tool's own runs left behind.

    A run of the plan writes into ``<tree>/benchmark_results`` — staged inputs,
    substituted configs, captured output — and those copies sit under the
    project's root, so an ``rglob`` over the project finds them.  They are not
    the project's files, and taking one is silent: measured on a hand-written
    make project, a previous run's copy of the program config sorted BEFORE
    the real ``bin/<config>``, the capture staged the stale copy, and the
    substitution it then declared was not in it — so every case died naming a
    file the project had all along.  The failure surfaced one run later, in a
    different place, with nothing pointing back here.

    Sorted, so the answer does not depend on the filesystem's enumeration
    order.
    """
    from protocolize.output_layout import RUN_OUTPUT_DIRS

    out: list[Path] = []
    for p in sorted(paths):
        if not (p.is_dir() if dirs else p.is_file()):
            continue
        if any(part in RUN_OUTPUT_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def _verification_from_case_dirs(proto: Protocol, case_dir: Path,
                                 build: Path, root: Path,
                                 rules: dict | None = None,
                                 proj_root_of: Path | None = None,
                                 facts: dict | None = None) -> dict:
    """One directory per case, staged whole.

    The directory is staged rather than the input alone, because a case's
    supporting decks and the restart state it hands forward sit beside it —
    staging just the input produces a case that cannot run, which then reads
    as a failure of the transpiler.
    """
    # A case directory is one that HOLDS the input its own id spells.  The
    # spelling is the corpus's — NASTRAN's `D01-00-0A` holds `d01000a.inp` —
    # and it is read here rather than guessed: a plan that said `{id}.inp`
    # discovered zero cases for that corpus.
    dirs = [d for d in sorted(case_dir.iterdir())
            if d.is_dir() and not d.name.startswith('.')]
    exclude, dispositions, name_t = _case_dir_rules(dirs)
    spelling = {}
    for d in dispositions:
        if d == 'lower':
            spelling['lower'] = True
        elif d.startswith('remove:'):
            spelling['remove'] = d.split(':', 1)[1]
    # Whether a case missing its input is skipped is the corpus's own
    # behaviour.  A corpus that excludes directories at all is one whose
    # discovery requires the input; one that excludes nothing is one that
    # treats every directory as a case.
    skip_missing = bool(exclude)

    product = ''
    for p in proto.products:
        if p.kind == 'exe':
            product = Path(p.output).name
            break
    return {
        'plan_version': 1,
        'target': {'kind': 'product', 'product': product,
                   'dirs': _target_dirs(proto)},
        'cases': {'kind': 'one_per_dir',
                  'root': _tree_spelling(case_dir, root),
                  'exclude': exclude,
                  'id': {'from': 'dirname'},
                  'input': {'name': name_t, 'id_spelling': spelling},
                  'skip_without_input': skip_missing},
        'inputs': {'from': 'case_dir',
                   'exclude_suffix': ['.out', '.sof1', '.txt']},
        'chains': _chains_from(case_dir, facts),
        # The corpus's own preparation, read from its tree: what the program
        # needs in order to RUN.  Captured rather than assumed — measured
        # without it, the program exits 0 and writes NOTHING.
        'staging': _staging_for(proj_root_of, case_dir, root, facts),
        'run': {'kind': 'process',
                'argv': ['{target}', '{input.name}'], 'cwd': '{work}',
                'exit_accept': [0, 2], 'case_timeout_s': 300},
        # The artifact is written into the case's WORK dir (the program's
        # cwd), which is where the corpus collects its outputs from.
        # Both of the case's outputs are artifacts: the report is what is
        # judged, and the restart state is what a chain hands forward.  A
        # `.sof1` left undeclared is invisible to the mechanism, which is how
        # a chain's handover would silently have nothing to carry.
        #
        # Spelled against `{work}` — the case's OWN directory — rather than
        # against the layout discovery happens to give it.  A hand-written
        # `work/{case_id}/...` is a case's location frozen at discovery, and
        # a restart chain moves its prerequisites into the chain's directory:
        # measured, the case ran there and produced its report there, while
        # the plan looked for it in the directory it used to be in, and the
        # chain's own members reported "no output".
        # WHAT each case writes, from the project's own declaration — never a
        # name baked in here.  A project that writes exactly one artifact
        # (every one the comparison reads) needs no declaration; one whose
        # run also hands state forward names it (a restart chain's state).
        'produced': _declared_produced(facts),
        'golden': {'source': 'reference_run', 'root': '{origin}',
                   'path': '{%s}/{input.stem}%s' % _golden_place(facts)},
        'assertions': _case_list_assertions(facts, rules),
        'report': {'repeats': 1},
        'summary': {'require_zero': ['diff', 'crash', 'timeout'],
                    'require_min': {'pass': 1}},
        'provenance': ('the golden is the reference build run through this '
                       'same plan, so an agreement proves stability, not '
                       'correctness — the corpus ships no reference output'),
        'comment': f'captured from {case_dir}',
    }


def _declared_produced(facts: dict | None) -> list[dict]:
    """A one-per-dir corpus's produced artifacts.

    A project that writes ONE artifact per case (what the comparison reads)
    needs no declaration.  A project whose run also writes state a chain
    hands forward, or writes under a different name, declares it — because
    what a program writes is a fact about that program.
    """
    spec = ((facts or {}).get('artifact') or {})
    suffix = spec.get('suffix', '.out')
    out = [{'name': '{work}/{input.stem}' + suffix, 'as': 'out'}]
    if spec.get('state_suffix'):
        out.append({'name': '{work}/{input.stem}' + spec['state_suffix'],
                    'as': 'state'})
    return out


def _golden_place(facts: dict | None) -> tuple[str, str]:
    """``(root_binding, suffix)`` for one golden, as the project saved it."""
    spec = ((facts or {}).get('artifact') or {})
    return (spec.get('golden_root', 'case_id'),
            spec.get('golden_suffix', spec.get('suffix', '.out')))


def _case_dir_rules(dirs) -> tuple[list[str], list[str], str]:
    """How a corpus names a case's input, derived from the corpus's OWN tree.

    Returns ``(excluded_names, spelling_dispositions, input_template)``.

    The rule is the normalisation that explains the MOST directories: a
    corpus's tree contains the cases and possibly a directory that is not one
    (NASTRAN's ``Archive``), so the test is coverage, not unanimity.  Measured:
    requiring every directory to fit rejected all four candidate spellings and
    fell through to a template that matched nothing, and only a glob fallback
    in the runner kept the corpus discoverable — a plan that works by accident
    is a plan that stops working silently.

    Deriving it rather than assuming it matters because guessing wrong is
    silent: every case simply fails to be found.  Measured: a plan that said
    ``{id}.inp`` discovered zero cases for this corpus, whose directory is
    ``D01-00-0A`` and whose input is ``d01000a.inp``.
    """
    # EVERY file in each directory, not the alphabetically first: a case
    # directory holds its input beside support files (``COSHYD1``,
    # ``rscards``, ``t03111a.optp``), and picking by name order marked 13
    # real cases as non-cases.  A directory is explained when ANY file in it
    # is what the rule spells.
    tree: list[tuple[str, list[Path]]] = []
    for d in dirs:
        files = sorted(f for f in d.iterdir()
                       if f.is_file() and not f.name.startswith('.'))
        if files:
            tree.append((d.name, files))
    if not tree:
        return [], [], '{id}.inp'

    candidates: list[tuple[list[str], object]] = [
        ([], lambda c: c),                              # verbatim
        (['lower'], str.lower),
    ]
    for ch in ('-', '_', '.'):
        candidates.append(([f'remove:{ch}'],
                           lambda c, ch=ch: c.replace(ch, '')))
        candidates.append((['lower', f'remove:{ch}'],
                           lambda c, ch=ch: c.lower().replace(ch, '')))

    def spell(cid: str, dispositions) -> str:
        out = cid
        if 'lower' in dispositions:
            out = out.lower()
        for d in dispositions:
            if d.startswith('remove:'):
                out = out.replace(d.split(':', 1)[1], '')
        return out

    scored = []
    for dispositions, _fn in candidates:
        hits, suffix = 0, ''
        for cid, files in tree:
            want = spell(cid, dispositions)
            for f in files:
                if f.stem == want:
                    hits += 1
                    suffix = suffix or f.suffix
                    break
        scored.append((hits, dispositions, suffix))
    hits, dispositions, suffix = max(scored, key=lambda t: t[0])
    if hits == 0:
        return [], [], '{id}.inp'

    explained = {cid for cid, files in tree
                 if any(f.stem == spell(cid, dispositions) for f in files)}
    # A directory the rule cannot explain is NOT a case — and that includes a
    # directory holding nothing at all, which is how NASTRAN's ``Archive`` is
    # excluded: the corpus's own discovery reads a fixed-named input inside
    # each directory and a directory without one is not a case.  Measured:
    # leaving it in discovered 133 where the corpus finds 132.
    excluded = [d.name for d in dirs
                if not d.name.startswith('.') and d.name not in explained]
    template = ('{stem}' if dispositions else '{id}') + (suffix or '.inp')
    return excluded, dispositions, template


def _id_spelling(sample_name: str) -> str:
    """The input's name with the case's own id put back as a placeholder."""
    base = sample_name.lower().replace('-', '').replace('_', '')
    if base and base in sample_name.lower().replace('-', '').replace('_', ''):
        stem = sample_name.rsplit('.', 1)[0]
        return '{id}' + sample_name[len(stem):]
    return sample_name
