"""A compile database that also knows how to link.

f2c needs one thing from a build: the compile database.  Everything it
converts is described by a per-file command, a working directory, a source
and an object, and that is the whole of the cc format.

The FLOW needs more, and none of it can be recovered from a compile
database:

* which units provide a Fortran module and which use one — a converted unit
  emits no ``.mod``, so a mixed build is only schedulable if that graph is
  known before anything is compiled;
* what the LINK products are, what goes into each, and in what order;
* which sources have to be GENERATED before any of it can compile.

So this is the compile database as ONE ELEMENT of a larger object rather than
a second file beside it.  On disk it is a JSON **object** whose
``compile_commands`` element is the compile entries — each carrying the fields
cc defines plus the extras below — and whose other keys carry the sections a
compile database cannot express.  A plain ``compile_commands.json`` is still
the legal weak form of a protocol
(:meth:`Protocol.from_compile_commands`), whose compilation half is exact and
whose products half is empty — so a consumer that needs a link line fails
loudly instead of inventing one.

It is NOT a compile database itself, and an earlier version of this file
claimed it was (a bare array of entries, on the theory that a cc consumer skips
what it does not recognise).  Measured against clangd 18.1.3, that theory is
false: the loader is a strict WHITELIST and all-or-nothing, so ONE entry
carrying `stem`, `language` or `output` makes it reject the ENTIRETY of the
file — the clean entries beside it included.  A valid compile database is
therefore an EXPORT (:meth:`Protocol.to_cc`), which drops every field cc does
not define, and :meth:`CompileEntry.as_compile_command` is the same four-field
view of a single entry.

The concept the flow side rests on is the **extension-independent stem**
(:func:`canonical_stem`).  Flipping a unit between its Fortran and C++ forms
must keep it the same link member: ``isamax.f`` and ``isamax.cpp`` are one
unit, and a key that carries the extension cannot say that — after the flip
the link line would name a member nobody builds, and the build would fail
with an undefined reference to a symbol that has no file to point at.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Sequence

#: The ``${TREE}`` placeholder and its expand/escape machinery live in
#: ``src.tree_anchor``: layer 1 (the transpiler's compile-database reader)
#: needs them without a protocol, so they are defined down there and
#: re-exported here for this module's consumers.
from protocolize.tree_anchor import (TREE, ProtocolError, capture_tree,  # noqa: F401
                             check_tree_invariant, escape_tree, expand_tree)

#: The one protocol version this code reads.  A file from another version is
#: refused rather than coerced: the format is the contract between the
#: onboarding skill and the flow, and guessing at a newer one would produce
#: a build that is subtly not what the file describes.
PROTOCOL_VERSION = 1

#: The key that marked, in the protocol's EARLIER array shape, the one element
#: carrying the sections.  It is still how that shape is told apart from a
#: plain compile database, which is why the name survives: an array whose
#: entries carry this key somewhere is an old protocol, and one without it is a
#: compile database.  Nothing writes it as an array element any more.
_NODE_KEY = 'protocol_version'

#: The keys a ``compile_commands.json`` entry is DEFINED to carry.  Anything
#: else is the protocol's own and is dropped by :func:`_cc_entry`.
_CC_KEYS = ('directory', 'file', 'command', 'arguments')


def _cc_entry(entry: 'CompileEntry') -> dict:
    """*entry* reduced to what a compile database defines, and nothing else."""
    full = entry.to_json()
    return {k: full[k] for k in _CC_KEYS if k in full}

_ANY_SUFFIX = re.compile(r'\.[A-Za-z][A-Za-z0-9]*$')


# ── stem identity ─────────────────────────────────────────────

def canonical_stem(stem: str) -> str:
    """The extension-independent identity of a translation unit.

    ``SRC/isamax.f`` and ``SRC/isamax.cpp`` are the same unit and the same
    link member, so the extension comes off.  Case is KEPT: Fortran is
    case-insensitive to the compiler but the build system's object naming is
    not, and normalizing here would make two units that a build keeps
    distinct collide.

    Only a TRAILING extension comes off, and only one: a directory with a
    dot in its name is part of the path.
    """
    return _ANY_SUFFIX.sub('', stem)


# ── the entries ───────────────────────────────────────────────

@dataclass(frozen=True)
class CompileEntry:
    """One translation unit: the standard four fields plus the superset."""

    directory: str
    #: The command line, as the standard format spells it.  A database MAY
    #: spell it as ``arguments`` instead and a protocol is a SUPERSET of that
    #: format, so a reader that takes one spelling and refuses the other is
    #: not a superset of anything.  Empty here means "the arguments carry it";
    #: a caller that needs a command line asks :meth:`command_line`.
    command: str
    file: str
    output: str = ''
    #: The command line as an ARGUMENT VECTOR, when the database spells it
    #: that way.  Kept as written rather than joined into ``command`` on the
    #: way in: an argument with a space in it is one argument here and two
    #: after a join, and re-splitting to find out which was meant is exactly
    #: the guessing the standard format exists to avoid.
    arguments: tuple[str, ...] = ()
    #: The extension-independent identity; see :func:`canonical_stem`.
    stem: str = ''
    #: Fortran modules this unit DEFINES.  A converted unit defines none,
    #: which is what makes the module graph schedulable.
    provides: tuple[str, ...] = ()
    #: Fortran modules this unit USES.
    uses: tuple[str, ...] = ()
    #: The source's LANGUAGE, a free-form label (``'fortran'``, ``'cpp'``,
    #: …).  Recorded because a consumer may need to know what it is compiling
    #: — the generator picks the CMake language from it — but it is NOT an
    #: identity: the stem is.  Older protocols spelled this ``variant`` and
    #: are still read.
    language: str = ''
    #: The PREPROCESS step, when the build has one.
    #:
    #: A build system may compile a Fortran source in two steps with
    #: different flags — CMake's scanned Fortran takes its defines from
    #: ``DEFINES`` on the preprocess edge and only ``FLAGS`` on the compile
    #: edge.  ``command`` alone is then not enough to reproduce the unit.
    preprocess: str = ''

    def __post_init__(self) -> None:
        # Normalize identity HERE rather than in from_json: a caller
        # constructing an entry directly must reach the same key, or "one
        # spelling for a stem" holds for only one of the two ways in.
        object.__setattr__(self, 'stem', canonical_stem(self.stem))

    def command_line(self) -> str:
        """The command as one line, whichever way it was spelled."""
        if self.command:
            return self.command
        import shlex
        return shlex.join(self.arguments)

    def to_json(self) -> dict:
        out: dict = {'directory': self.directory, 'file': self.file}
        if self.command:
            out['command'] = self.command
        elif self.arguments:
            out['arguments'] = list(self.arguments)
        if self.output:
            out['output'] = self.output
        if self.stem:
            out['stem'] = self.stem
        if self.provides:
            out['provides'] = list(self.provides)
        if self.uses:
            out['uses'] = list(self.uses)
        if self.language:
            out['language'] = self.language
        if self.preprocess:
            out['preprocess'] = self.preprocess
        return out

    @classmethod
    def from_json(cls, d: dict) -> 'CompileEntry':
        # The standard format allows EITHER ``command`` (a string) or
        # ``arguments`` (a vector), and a protocol is its superset — so both
        # are read here.  Refusing the vector spelling made the reader reject
        # a file it claims to be a superset of, which is how the
        # per-file-flags test went from passing to dead.
        args = tuple(str(a) for a in (d.get('arguments') or ()))
        if not d.get('command') and not args:
            raise ProtocolError(
                f'compile entry has neither command nor arguments: {d!r}')
        missing = [k for k in ('directory', 'file') if not d.get(k)]
        if missing:
            raise ProtocolError(
                f'compile entry missing {", ".join(missing)}: {d!r}')
        return cls(
            directory=d['directory'], command=d.get('command') or '',
            arguments=args, file=d['file'],
            output=d.get('output') or '',
            stem=canonical_stem(d.get('stem') or ''),
            provides=tuple(d.get('provides') or ()),
            uses=tuple(d.get('uses') or ()),
            language=(d.get('language') or d.get('variant') or ''),
            preprocess=d.get('preprocess') or '',
        )

    def as_compile_command(self) -> dict:
        """The entry as a plain ``compile_commands.json`` object.

        This is the projection that makes the protocol a superset: a
        consumer that only understands the standard format reads these and
        ignores the rest.
        """
        return {'directory': self.directory, 'command': self.command_line(),
                'file': self.file, 'output': self.output}


# ── the products ──────────────────────────────────────────────

@dataclass(frozen=True)
class Product:
    """A link product: what it is, and what goes into it."""

    output: str
    kind: str                      # 'archive' | 'exe'
    #: Whether an ``archive`` product is a SHARED library (a ``.so``/``.dylib``)
    #: rather than a static one.  Load-bearing for a replay: linking a shared
    #: library leaves its symbols UNDEFINED in the consumer, while linking a
    #: static one pulls them INTO it — so rebuilding a shared library as static
    #: changes every consumer's defined-symbol set.  Measured on a project
    #: that builds its library shared by default.
    shared: bool = False
    #: What goes INTO this product, by STEM — the unit→product edges a
    #: compile database cannot carry.  One field for both kinds, because the
    #: relation is one relation: an archive's members and an executable's own
    #: units are both "these units are inputs to this product", and ``kind``
    #: already says which reading applies.  Splitting them into ``members``
    #: and ``objects`` was two names for one thing, and nothing could tell
    #: you which to fill in — the answer was always "whichever ``kind`` is".
    #:
    #: The value is the STEM, never a file name: a member spelled
    #: ``isamax.f.o`` pins the link line to one form, and a unit converted to
    #: C++ would then drop out of the archive while the line still asked for
    #: it.  ``libs`` is the contrast that makes the naming matter — a library
    #: is a PATH, because it may be one we did not build and have no stem for.
    stems: tuple[str, ...] = ()
    libs: tuple[str, ...] = ()
    driver: str = ''
    flags: tuple[str, ...] = ()
    #: The EXACT command that produces this product, as the build ran it.  A
    #: replay runs this verbatim rather than re-deriving a link line: the
    #: recorded command is the authority on flags, library order and search
    #: paths, the same way a unit's ``command`` is for its compile.  Empty
    #: when the build system did not expose it.
    command: str = ''
    #: The directory :attr:`command` ran in — the link line's relative
    #: operands are relative to IT (a Makefile links in the directory it runs
    #: in).  Empty means the tree root.
    directory: str = ''
    #: RESPONSE FILES this command reads, by the path the command spells —
    #: `@<path>` operands a build generator writes just before the edge runs
    #: and deletes after.  Only the FILE is recorded; its content is the
    #: inputs the protocol already knows (`stems`), one per line, which is
    #: what a generator puts there for an archive edge.
    #:
    #: Recorded because a replay cannot re-derive it: nothing declares the
    #: file as an output, so it looks exactly like a file the project ships,
    #: and a replay that treats it as one reads the capture tree.  Measured on
    #: a corpus whose archive edge alone used one: the replay built 6,794
    #: units and died archiving the one library, against a path that existed
    #: in neither tree.
    rspfiles: tuple[str, ...] = ()

    def to_json(self) -> dict:
        out: dict = {'output': self.output, 'kind': self.kind}
        if self.shared:
            out['shared'] = True
        for k in ('stems', 'libs', 'flags'):
            v = getattr(self, k)
            if v:
                out[k] = list(v)
        if self.driver:
            out['driver'] = self.driver
        if self.command:
            out['command'] = self.command
        if self.directory:
            out['directory'] = self.directory
        if self.rspfiles:
            out['rspfiles'] = list(self.rspfiles)
        return out

    @classmethod
    def from_json(cls, d: dict) -> 'Product':
        missing = [k for k in ('output', 'kind') if not d.get(k)]
        if missing:
            raise ProtocolError(
                f'product missing {", ".join(missing)}: {d!r}')
        # `members` and `objects` are read as well as `stems`: they were two
        # names for this one field, and a protocol written before the merge
        # carries whichever its producer filled in.  Reading both means an
        # older file loads without being re-admitted, and the concatenation
        # is the same relation the current shape states once.
        return cls(output=d['output'], kind=d['kind'],
                   shared=bool(d.get('shared')),
                   stems=tuple(d.get('stems') or ())
                   + tuple(d.get('members') or ())
                   + tuple(d.get('objects') or ()),
                   libs=tuple(d.get('libs') or ()),
                   driver=d.get('driver') or '',
                   flags=tuple(d.get('flags') or ()),
                   command=d.get('command') or '',
                   directory=d.get('directory') or '',
                   rspfiles=tuple(d.get('rspfiles') or ()))


#: The keys the `build` section may carry.  All are OPTIONAL: a project
#: declares only the ones it has, and the section is `{}` for a project that
#: declares none.  Nothing in this tool branches on any of them — they are
#: extras a CONSUMER may read, not facts the mechanism requires.
BUILD_SECTIONS: tuple[str, ...] = ('flags', 'module_dirs', 'include_dirs')


def normalise_build(build: dict | None) -> dict:
    """The `build` section with its fixed shape, whatever a producer supplied.

    Flattens the two spellings a producer may use for the optimization map --
    `{'flags': {'optimize': {...}}}` (the template) and a top-level
    `{'optimize': {...}}` (a config did it that way).

    Every field is OPTIONAL and an empty one is left OUT, so a project's
    `build` is exactly what it declares: `{}` when it declares nothing.  The
    fields are toolchain-specific extras (an optimization remap, module and
    include directories) — a general project has none of them, and no generic
    code branches on them.
    """
    b = dict(build or {})
    flags = dict(b.get('flags') or {})
    optimize = dict(flags.get('optimize') or b.pop('optimize', None) or {})
    policy = [str(x) for x in (flags.get('policy') or ())]
    out: dict = {}
    if optimize or policy:
        inner: dict = {}
        if optimize:
            inner['optimize'] = {str(k): str(v) for k, v in optimize.items()}
        if policy:
            inner['policy'] = policy
        out['flags'] = inner
    for key in ('module_dirs', 'include_dirs'):
        vals = [str(x) for x in (b.get(key) or ())]
        if vals:
            out[key] = vals
    return out


# ── the protocol ──────────────────────────────────────────────

@dataclass
class Protocol:
    """A whole build: its translation units, its products, its generated sources."""

    compilation: list[CompileEntry] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    #: Sources that must be GENERATED before anything can compile.  Named
    #: here rather than discovered, because naming a source that does not
    #: exist yet and having nothing to create it is a build that cannot start.
    generated_inputs: list[dict] = field(default_factory=list)
    #: The corpus's OWN source roots, in the protocol's own spelling.
    #:
    #: A corpus is vendored TOGETHER WITH the third-party libraries its build
    #: links, and those are built into ARCHIVE products rather than converted.
    #: WHICH units are the corpus's is a fact about the project, so it is
    #: established where the project is read — by the onboarding skill, from
    #: the build's own products and the inputs its generated sources were made
    #: from — and recorded here.  A consumer that re-derives it from a config
    #: field is computing an answer the protocol already holds, which is how
    #: the same corpus came to be scoped two ways.
    #:
    #: Empty means the corpus declares no narrowing: every unit is its own,
    #: which is the case for a corpus whose build has no vendored libraries.
    own_roots: tuple[str, ...] = ()
    #: The corpus's own TEST registrations, as the build system reports them.
    #:
    #: A build that can be generated must also be RUNNABLE the way the
    #: project runs it, and for a self-verifying corpus that means its test
    #: suite: LAPACK's drivers check their own residuals and the aggregate
    #: table is the corpus's verdict, so a build with no registrations
    #: cannot be verified at all.  Measured: the generated tree built
    #: 7045/7045 objects and the benchmark died in 0.022s with "no CTest
    #: registration", because the registrations live in the project's own
    #: CMakeLists and this generator writes a new one from the protocol.
    #:
    #: Captured through the build system's OWN interface (`ctest
    #: --show-only=json-v1`), never by parsing a generated Testfile: the
    #: file is CMake's internal format and its escapes and ordering are
    #: CMake's.
    tests: list[dict] = field(default_factory=list)
    #: Files the build must HAVE that it does not compile and does not
    #: generate: each is ``{path, content}``, *path* under the tree.
    #:
    #: A test registration names files that are neither units nor build
    #: products — LAPACK's ``lapack_testing.py`` (the suite's own aggregator,
    #: which the project's CMake ``file(COPY)``s into the build) and the eight
    #: ``*_64.in`` inputs its CMake derives from the corpus's own ``.in`` by
    #: substituting output names.  Both are made by CONFIGURE-time CMake, so
    #: neither is a build edge and neither can be recovered from
    #: ``generated_inputs``.
    #:
    #: Carried as CONTENT rather than as a command, because there is no
    #: command: the derivation belongs to the project's CMakeLists and
    #: reimplementing its ``string(REPLACE)`` here would be a second, worse
    #: implementation of something CMake already did.  Measured: 9 files,
    #: ~101 KB — the registry's own inputs, not a copy of the project.
    #:
    #: Only what a test READS is captured.  Its ``-DOUTPUT=`` files are
    #: results and are deliberately absent: writing them would hand the
    #: suite a run it did not perform.
    support_files: list[dict] = field(default_factory=list)
    #: WHERE the protocol was captured from and what that tree IS.  Facts a
    #: compile database cannot carry, and which a consumer MUST NOT guess:
    #: ``tree`` is what ``${TREE}`` denotes (an out-of-source build's tree is
    #: the build directory, one level below the source checkout, and the
    #: difference silently changes where every generated source lands);
    #: ``layout`` says whether the build ran in the source tree or beside it.
    capture: dict = field(default_factory=dict)
    #: Infrastructure the corpus needs ONCE for the repeated builds and runs
    #: that follow -- a `git submodule` fetch, a portability patch, an overlay
    #: copy.  Each op carries a CHECK (the corpus's own way of telling it is
    #: already done), so it runs AT MOST ONCE and a repeat is a no-op; the
    #: build is reproducible from a raw checkout without paying for the setup
    #: on every build.  Empty when the corpus needs none.
    prepare: list[dict] = field(default_factory=list)
    #: Build facts a compile database cannot carry: the optimization level
    #: the CONVERTED code is built at (which may differ from the source's),
    #: the policy flags every unit gets, and the include/module directories
    #: the build exports.  Empty fields mean the corpus declares none.  The
    #: SHAPE is fixed (:func:`normalise_build`), so a consumer never branches
    #: on which fields this corpus happened to spell.
    build: dict = field(default_factory=lambda: normalise_build(None))
    version: int = PROTOCOL_VERSION
    comment: str = ''

    # ── projections ───────────────────────────────────────────
    def to_compile_commands(self) -> list[dict]:
        """The standard-format view: what a cc-only consumer reads."""
        return [e.as_compile_command() for e in self.compilation]

    @classmethod
    def from_compile_commands(cls, entries: Sequence[dict], *,
                              stems: dict[str, str] | None = None) -> 'Protocol':
        """Adopt a plain compile database as the protocol's weak form.

        A corpus whose build system exports no link information still gets a
        usable protocol: the compilation half is exact and the products half
        is empty (a consumer that needs it must fail loudly rather than
        invent a link line).  *stems* optionally supplies identities the
        database itself cannot express.
        """
        stems = stems or {}
        return cls(compilation=[
            CompileEntry.from_json(
                {**e, 'stem': stems.get(e.get('file', ''), '')})
            for e in entries])

    # ── serde ─────────────────────────────────────────────────
    def to_cc(self) -> list:
        """This protocol as a plain ``compile_commands.json`` ARRAY.

        EVERY field is dropped except the ones a compile database DEFINES —
        ``directory``, ``file``, and ``command``/``arguments``.

        That filtering is the whole job, and it is not cosmetic.  Measured
        against clangd 18.1.3: a compile-database loader is a strict WHITELIST
        and all-or-nothing.  One entry carrying `stem` (or `language`, or
        `output`) makes it reject the ENTIRE file with ``Unknown key`` — the
        clean entries beside it become unreadable too.

        So the stored form is not a compile database and does not claim to be
        one (see :meth:`to_json`).  This is the EXPORT, and it is honest about
        being a projection: what it returns is valid cc; what is on disk is the
        protocol.
        """
        return [_cc_entry(e) for e in self.compilation]

    def to_json(self) -> dict:
        # EVERY top-level section is always written, empty ones as [].  The
        # protocol's shape is a CONTRACT between the skill and the flow, and a
        # section that appears only when a corpus happens to use it makes two
        # corpora produce two different shapes for the same kind of fact —
        # measured: NASTRAN omitted `generated_inputs`/`own_roots`/`tests`
        # entirely while LAPACK carried all three, so a consumer could not
        # tell "this corpus has none" from "this reader did not look".
        #
        # `compile_commands` is ONE ELEMENT of this object, not the object
        # itself.  A compile database is something this protocol CONTAINS and
        # can export (:meth:`to_cc`), not something it IS: the entries carry
        # fields cc does not define (`stem`, `language`, `output`), and a
        # cc loader rejects a file over any one of them.
        return {
            'protocol_version': self.version,
            'capture': dict(self.capture or {}),
            'compile_commands': [e.to_json() for e in self.compilation],
            'generated_inputs': [dict(g) for g in self.generated_inputs],
            'products': [p.to_json() for p in self.products],
            'build': normalise_build(self.build),
            'prepare': [dict(x) for x in (self.prepare or ())],
            'own_roots': list(self.own_roots or ()),
            'tests': list(self.tests or ()),
            'support_files': list(self.support_files or ()),
            'comment': self.comment or '',
        }

    @classmethod
    def from_json(cls, d: 'dict | list') -> 'Protocol':
        """Read a protocol from EITHER shape it is written in.

        The on-disk shape is an OBJECT whose ``compile_commands`` element is
        the compile entries and whose other keys are the sections a compile
        database cannot express.  The OLDER array shape still reads — entries
        followed by a node — so every protocol captured before the change
        loads without being re-admitted, and a bare compile database (an array
        with NO node) is still the legal weak form.
        """
        if isinstance(d, list):
            node = next((e for e in d
                         if isinstance(e, dict) and _NODE_KEY in e), None)
            entries = [e for e in d if e is not node]
            if node is None:             # a plain compile_commands.json
                return cls.from_compile_commands(entries)
            return cls._from_object({**node, 'compile_commands': entries})
        if not isinstance(d, dict):
            raise ProtocolError(
                f'protocol must be an object or an array, got '
                f'{type(d).__name__}')
        # `compilation` is the pre-`compile_commands` name of the same
        # element, read for the same reason the array shape is.
        if 'compile_commands' not in d and 'compilation' in d:
            d = {**d, 'compile_commands': d['compilation']}
        return cls._from_object(d)

    @classmethod
    def _from_object(cls, d: dict) -> 'Protocol':
        """The protocol, from the object that carries every section."""
        ver = d.get('protocol_version', 0)
        if ver != PROTOCOL_VERSION:
            raise ProtocolError(
                f'protocol_version {ver!r} is not {PROTOCOL_VERSION}')
        comp = d.get('compile_commands')
        if comp is None:
            raise ProtocolError(
                "protocol has no 'compile_commands' section")
        return cls(
            compilation=[CompileEntry.from_json(e) for e in comp],
            products=[Product.from_json(p) for p in d.get('products') or ()],
            generated_inputs=list(d.get('generated_inputs') or ()),
            own_roots=tuple(d.get('own_roots') or ()),
            tests=list(d.get('tests') or ()),
            support_files=list(d.get('support_files') or ()),
            capture=dict(d.get('capture') or {}),
            build=normalise_build(d.get('build')),
            prepare=list(d.get('prepare') or ()),
            version=ver,
            comment=d.get('comment') or '',
        )

    def save(self, path: Path | str) -> None:
        """Write the protocol as a JSON OBJECT, in plain JSON.

        PLAIN and ungzipped even where the path says otherwise: a gzipped file
        is not one a readable-by-eye artifact should be.  (A protocol committed
        gzipped still LOADS — see :meth:`load` — this only governs what is
        written.)
        """
        Path(path).write_text(
            json.dumps(self.to_json(), indent=1, sort_keys=False),
            encoding='utf-8')

    @classmethod
    def load(cls, path: Path | str) -> 'Protocol':
        """Read the protocol, whether it is plain JSON or committed gzipped."""
        raw = Path(path).read_bytes()
        if raw[:2] == b'\x1f\x8b':       # the gzip magic number
            return cls.from_json(json.loads(gzip.decompress(raw).decode('utf-8')))
        return cls.from_json(json.loads(raw.decode('utf-8')))

    def problems(self) -> list[str]:
        """Everything wrong with this protocol as written; ``[]`` is valid.

        Deliberately a list rather than a raise: a caller diagnosing an
        onboarding run wants every gap at once, not the first one.
        """
        bad: list[str] = []

        seen: dict[tuple, int] = {}
        for i, e in enumerate(self.compilation):
            for name in ('directory', 'command', 'file', 'output'):
                why = check_tree_invariant(getattr(e, name))
                if why:
                    bad.append(f'compilation[{i}].{name}: {why}')
            # ONE UNIT PER (stem, language).  Two languages may share a stem —
            # a Fortran module and the C it binds are distinct units — but two
            # entries of the SAME language for one stem compile twice and
            # archive under one name.
            key = (e.stem, e.language or '')
            if key in seen:
                bad.append(
                    f'compilation[{i}]: stem {e.stem!r} appears twice as '
                    f'{key[1] or "?"} — two units of one language sharing a '
                    f'stem compile twice and archive under one name')
            seen[key] = i

        for i, g in enumerate(self.generated_inputs):
            for name in ('output', 'command'):
                if not g.get(name):
                    bad.append(
                        f'generated_inputs[{i}] has no {name!r} — a rule that '
                        f'names no source cannot be run')

        defined = {e.stem for e in self.compilation}
        for i, p in enumerate(self.products):
            for name in ('output',):
                why = check_tree_invariant(getattr(p, name))
                if why:
                    bad.append(f'products[{i}].{name}: {why}')
            for m in p.stems:
                if m not in defined:
                    bad.append(
                        f'products[{i}] {p.output} needs {m!r}, which no '
                        f'compile entry defines — from-scratch this is the '
                        f'last and most confusing failure to reach')

        # A prepare op without its CHECK would run its command on every
        # build, which is the shape this section exists to avoid; one
        # without a command but with a check is a statement that cannot be
        # made true.  Both are refused rather than run.
        for i, op in enumerate(self.prepare):
            if not op.get('check'):
                bad.append(
                    f'prepare[{i}]: a prepare op with no `check` runs on '
                    f'every build — the check is how it stays idempotent')
            if not op.get('run'):
                bad.append(
                    f'prepare[{i}]: a prepare op with no `run` can never '
                    f'satisfy its own check')
        return bad
