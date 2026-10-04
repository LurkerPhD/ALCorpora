"""Read a protocol out of a build driven by GNU make.

The ninja path reads the build through ninja's own sub-tools, which is what
makes it exact rather than a manifest parser.  A hand-written Makefile has
none of those, and `make`'s equivalent is just as official:

    make -Bn <target>

Three things about that command carry this whole module, and each one fails
silently if it is gotten wrong:

**``-B`` is not optional.**  Without it, ``make -n`` on an up-to-date tree
prints "Nothing to be done" and the extraction comes back EMPTY — no error,
just nothing, which reads exactly like a project with no translation units.

**The target must be named.**  A Makefile's default target may build fewer
products than the build has, so ``all`` is asked for first and the default is
only a fallback.

**``$(shell ...)`` does NOT expand under ``-n``.**  A Makefile that generates
sources through a shell command will not show the rule here, while the files
themselves exist on disk because the build made them.  That gap is DETECTED
and refused (:func:`_refuse_missing_sources`) rather than papered over,
because a protocol naming a source nothing produces fails at the first
compile of a generated build.

What a make build cannot do is compare objects byte for byte — see
:data:`OBJECTS_NOT_COMPARABLE`.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from protocolize.private_protocol import TREE, CompileEntry, Product, Protocol

from .extract import ExtractionError, Toolchain, _absolutise_paths

__all__ = ['OBJECTS_NOT_COMPARABLE', 'extract_make_protocol',
           'make_directories']

#: Why a make-built project's objects cannot be matched byte for byte.
#:
#: A Fortran object embeds the source path AS SPELLED ON THE COMMAND LINE.
#: make runs in the directory holding the sources and names them relatively
#: (``-c -o a42a8.o a42a8.f``, embedding ``a42a8.f``); CMake passes an
#: absolute path and offers no option to do otherwise.  So the two can never
#: agree, whatever the trees look like.
#:
#: Measured on NASTRAN-95: recompiling a unit the make way reproduces the
#: reference object EXACTLY (9 of 9 sampled, with the corpus's own flags
#: including ``-Jmod`` and ``-nocpp``), and naming the source absolutely does
#: not.  The only difference is that spelling.
OBJECTS_NOT_COMPARABLE = (
    "the project's own build is make, which names its sources RELATIVELY to "
    "the directory it runs in, and every object embeds that spelling.  CMake "
    "passes an absolute path and has no option to do otherwise, so the two "
    "cannot match byte for byte however the trees are laid out.  The INPUTS "
    "are compared exactly and are the meaningful check here.")

#: Extensions a source can carry.  A token with one of these in a link line
#: is a source compiled directly into the product.
_SOURCE_SUFFIXES = ('.f', '.f90', '.F', '.F90', '.for', '.FOR', '.f77',
                    '.f95', '.f03', '.f08', '.c', '.cpp', '.cc', '.cxx')

#: The targets tried, in order, for each make directory.  `all` first,
#: because the default target may build a strict subset.
_TARGETS = ('all', '')

#: Compiler driver basenames.  Matched by PREFIX on the executable's own
#: basename, so `/usr/bin/gfortran` and a variable holding `mpif90` are both
#: recognised.
_COMPILERS = ('gfortran', 'gcc', 'g++', 'cc', 'c++', 'f95', 'f90', 'ifort',
              'ifx', 'flang', 'mpif90', 'mpicc', 'mpicxx', 'clang')

#: `ar`'s own basenames, including the llvm spelling.
_ARCHIVERS = ('ar', 'llvm-ar')


def _has_makefile(d: Path) -> bool:
    return (d / 'Makefile').is_file() or (d / 'makefile').is_file()


def make_directories(tree: Path) -> list[Path]:
    """Every directory that has a Makefile: the root, then its children.

    ALL of them, not "the root if there is one".  A root Makefile is not
    evidence that it is the whole build — measured on NASTRAN-95, whose root
    Makefile compiles exactly ONE file (a shared library the executables
    need) while 1,872 units are built by `mis/`, `mds/` and `bd/`.  Reading
    only the root yielded a protocol with one unit, and it looked like a
    project with one unit rather than like a gap.

    Not recursive beyond one level, deliberately: a project with a Makefile
    per source directory would otherwise be descended into looking for
    builds that are not there, and every one of them would be asked to run
    ``make -n``.  One level covers the layout that occurs — a root for the
    common pieces and a directory per component — and the products of a
    build that nests deeper say so, since a link edge naming an object no
    directory compiled is refused rather than dropped.
    """
    out: list[Path] = []
    if not tree.is_dir():
        # A tree that is not there is an ANSWER — "this is not a make
        # directory" — not a crash.  Measured while re-admitting a make
        # project: the flow's `tree_subdir` default is `build` (right for the
        # CMake default commands, which is what it pairs with), and against a
        # hand-written make project the capture died with a `FileNotFoundError`
        # traceback out of `iterdir` before any seam could open.  The seam
        # exists to be asked; it cannot be asked if the flow dies first.
        return out
    if _has_makefile(tree):
        out.append(tree)
    out.extend(sorted(d for d in tree.iterdir()
                      if d.is_dir() and _has_makefile(d)))
    return out


def _dry_run(directory: Path) -> list[str]:
    """The commands *directory*'s Makefile would run, without running them."""
    last: Exception | None = None
    for target in _TARGETS:
        argv = ['make', '-Bn', *((target,) if target else ())]
        try:
            r = subprocess.run(argv, cwd=str(directory), capture_output=True,
                               text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            last = ExtractionError(
                f'`make -Bn` timed out after 1800s in {directory}')
            continue
        if r.returncode == 0:
            return [line.strip() for line in r.stdout.splitlines()
                    if line.strip()]
        last = ExtractionError(
            f'`{" ".join(argv)}` failed in {directory}: '
            f'{r.stderr.strip()[:400]}')
    raise last if last else ExtractionError(f'no Makefile in {directory}')


def _produced(tc: Toolchain, directories: list[Path]) -> frozenset:
    """Every path this make build writes.

    Asked of the dry run because that is what a make build will tell you:
    ``make -Bn all`` prints the commands, and the commands name their own
    outputs.  A path named as an ``-o`` operand, or as the destination of an
    ``ar``/``ld`` invocation, IS produced; everything else the commands name
    is read.

    Left OUT when unrecognised, which errs toward spelling a produced path
    absolutely — visible, and a replay writes into the wrong tree — rather
    than re-rooting a read path, which names a copy that never existed.  The
    second is worse: it is silent.
    """
    out: set[str] = set()
    for d in directories:
        for line in _dry_run(d):
            for tok in _output_operands(line):
                # Relative to the DIRECTORY make ran in, not to the tree:
                # `mis/Makefile`'s `-o libnastran.a` names `mis/libnastran.a`.
                # Resolving against the tree instead gives `<tree>/libnastran.a`
                # — a path that is not the product, so the product stays out
                # of this set and is spelled absolutely (measured: 4 of 4).
                out.add(str(tc.to_abs(d / tok)))
    return frozenset(out)


def _output_operands(line: str) -> list[str]:
    """The paths *line* writes, by the conventions a compile or link uses.

    * ``-o X`` — a compiler, or a linker.
    * ``ar crs lib.a a.o …`` — the archive is the first non-flag token that
      is not the modifier bundle.  (The ``-o`` form is handled above; both
      occur, and reading only one of them refuses or misreads the other.)
    """
    try:
        argv = shlex.split(line)
    except ValueError:
        return []
    try:
        argv = shlex.split(line)
    except ValueError:
        return []
    # An `ar` line first, and ONCE, because it is the case `-o` alone reads
    # wrong: `ar cr -o lib.a a.o b.o` has an `-o` whose operand is the
    # archive, but a plain `ar crs lib.a a.o b.o` has no `-o` at all and its
    # archive is the first token after the modifier bundle.  Scanning for
    # `-o` throughout would take every `-o`-looking token and miss the
    # positional form entirely.
    if argv and Path(argv[0]).name.startswith('ar'):
        j = 1
        while j < len(argv) and (argv[j].startswith('-')
                                 or _is_ar_modifier(argv[j])):
            if argv[j] == '-o' and j + 1 < len(argv):
                return [argv[j + 1]]        # the archive
            j += 1
        return [argv[j]] if j < len(argv) else []

    out: list[str] = []
    for i, tok in enumerate(argv):
        if tok == '-o' and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


def _is_ar_modifier(tok: str) -> bool:
    """Is *tok* an ``ar`` modifier bundle (``crs``, ``rc``, …)?"""
    return bool(tok) and all(c in 'dmpqrstxabcDfilNoOPSTuUvV' for c in tok)


def _is_source(token: str) -> bool:
    return token.endswith(_SOURCE_SUFFIXES)


def _is_archive(token: str) -> bool:
    return token.endswith('.a')


def _is_compiler(line: str) -> bool:
    """Is *line* a compiler invocation rather than a shell utility?

    A Makefile runs ``mkdir``, ``rm``, ``sed`` and worse around its compiles,
    and only the compiler's own commands describe translation units.  Asked
    on the executable's BASENAME, so a Makefile that reaches its compiler
    through a variable holding ``mpif90`` is still recognised.
    """
    tokens = line.split()
    if not tokens:
        return False
    name = Path(tokens[0]).name
    if name in _ARCHIVERS or name == 'ranlib':
        return True
    return any(name.startswith(p) for p in _COMPILERS)


def extract_make_protocol(tree: str | Path, *,
                          tree_root: str | Path | None = None,
                          comment: str = '') -> Protocol:
    """The private protocol describing the make build in *tree*.

    *tree* must ALREADY be built — the commands are read back out of what the
    project's own toolchain would run.  Raises :class:`ExtractionError`
    naming the gap when the build cannot be expressed as a protocol.
    """
    tree_path = Path(tree).resolve()
    if not tree_path.is_dir():
        raise ExtractionError(f'{tree_path} is not a directory')
    directories = make_directories(tree_path)
    if not directories:
        raise ExtractionError(
            f'{tree_path} has no Makefile and no subdirectory with one, so '
            f'there is no make build to read')

    root = Path(tree_root).resolve() if tree_root else tree_path
    tc = Toolchain(build=tree_path, root=root)
    # What this build PRODUCES — the same rule the ninja backend follows, and
    # for the same reason: a path's meaning decides its spelling, so a
    # product left out of this set is spelled ABSOLUTELY and a replay writes
    # it into the capture tree.  Measured on NASTRAN-95: all four products
    # (the archive and three executables) came out absolute.
    tc.produced = _produced(tc, directories)

    entries: dict[str, CompileEntry] = {}
    products: list[Product] = []
    # Object -> stem, filled as compiles are seen, so a link that names an
    # object can resolve it to the stem that produces it.
    stem_of_object: dict[str, str] = {}
    # Link and archive edges are held back until every compile is known: a
    # link names objects that a LATER make directory may compile, so
    # resolving as they are seen would miss those.
    links: list[tuple[Path, list[str]]] = []
    archives: list[tuple[Path, tuple[Path, list[str]]]] = []

    for directory in directories:
        for line in _dry_run(directory):
            tokens = line.split()
            if not tokens:
                continue
            if Path(tokens[0]).name in (*_ARCHIVERS, 'ranlib'):
                ar = _parse_archive(line, directory)
                if ar is not None:
                    archives.append((directory, ar))
                continue
            if not _is_compiler(line):
                continue
            try:
                argv = shlex.split(line)
            except ValueError as exc:
                raise ExtractionError(
                    f'unparsable command from {directory}: {exc}') from exc
            if '-c' in argv:
                parsed = _parse_compile(tc, line, argv, directory)
                if parsed is not None:
                    entry, obj_abs = parsed
                    # Keyed on the RESOLVED object path, never on the
                    # protocol's spelling of it: that spelling is
                    # `${TREE}/...`, which resolves to nothing useful.
                    stem_of_object[str(obj_abs)] = entry.stem
                    entries.setdefault(entry.stem, entry)
                continue
            links.append((directory, argv))

    if not entries:
        raise ExtractionError(
            'the make build runs no compile commands, so nothing here '
            'describes a translation unit')

    # Sources compiled INSIDE a link line have no object of their own until
    # the link runs, so they are units too.  Their object is named after the
    # stem, which is what a flat CMake build produces for them as ordinary
    # target sources.
    for directory, argv in links:
        for token in _link_sources(argv):
            # Relative to the make DIRECTORY, not to the tree: a Makefile's
            # own relative paths are the ones it runs with, and `make` was
            # invoked in this directory.
            src = Path(token)
            src = src if src.is_absolute() else (directory / src)
            if not src.is_file():
                raise ExtractionError(
                    f'{directory} links a source that does not exist: {src}')
            stem = tc.stem_of(src)
            # The OBJECT is named after the SOURCE's basename (`nastran.o`),
            # which is what the compiler writes in the make directory — NOT
            # the tree-relative stem (`mds/nastran.o`), which would apply the
            # subdirectory a SECOND time once the command runs there.
            obj = src.stem + '.o'
            entries.setdefault(stem, CompileEntry(
                directory=tc.dir_spelling(directory),
                command=_synthetic_compile(argv, src, obj),
                file=tc.in_tree_space(src), output=obj, stem=stem,
                language='cpp' if src.suffix in ('.c', '.cpp', '.cc', '.cxx')
                else 'fortran'))

    # Archives first, so a link that names one can find its members.
    members_of_archive: dict[str, tuple[str, ...]] = {}
    for directory, (archive_path, member_tokens) in archives:
        stems = _resolve_objects(tc, directory, member_tokens, stem_of_object)
        members_of_archive[str(archive_path)] = tuple(stems)
        # The recorded `ar` line, so the replay builds the archive itself
        # rather than only naming it (see _parse_link's note).
        ar_line = _archive_line(directory, archive_path, member_tokens)
        products.append(Product(
            output=tc.in_tree_space(archive_path), kind='archive',
            stems=tuple(stems),
            command=(tc.in_tree_space_cmd(
                _absolutise_paths(ar_line, str(directory)), directory)
                if ar_line else ''),
            directory=tc.dir_spelling(directory)))

    for directory, argv in links:
        product = _parse_link(tc, directory, argv, stem_of_object,
                              members_of_archive)
        if product is not None:
            products.append(product)

    if not products:
        raise ExtractionError(
            'the make build produces no archive and links no executable, so '
            'a state-driven build could produce nothing from it')

    _refuse_missing_sources(entries)
    return Protocol(compilation=list(entries.values()),
                    generated_inputs=[],
                    products=products,
                    comment=comment or f'onboarded from {tree_path}')


# ── parsing ───────────────────────────────────────────────────

def _parse_compile(tc: Toolchain, line: str, argv: list[str],
                   directory: Path) -> tuple[CompileEntry, Path] | None:
    """One ``-c -o <obj> <src>`` command as ``(entry, absolute object)``.

    The absolute object comes back alongside the entry because the entry
    spells it in the PROTOCOL's space, and a link edge naming a real object
    path has to be matched against something resolvable.  Deriving one from
    the other would mean parsing the placeholder back out.
    """
    if '-o' not in argv:
        return None
    i = argv.index('-o')
    if i + 2 > len(argv):
        return None
    obj = argv[i + 1]
    # The source is the first non-flag token after `-o`.  `make -n` emits
    # `-c -o obj src`, but a Makefile may order the flags differently.
    rest = [t for t in argv[i + 2:] if not t.startswith('-')]
    if not rest:
        return None
    src = Path(rest[0])
    src_path = src if src.is_absolute() else (directory / src)
    if not src_path.is_file():
        # A source that does not exist is either generated by something this
        # dry run cannot see (a `$(shell ...)` generator), or missing.
        # Either way the protocol cannot describe it, and saying so later
        # beats emitting a unit that cannot be built.
        return None
    obj_path = Path(obj)
    obj_out = (obj_path if obj_path.is_absolute() else directory / obj_path)
    return CompileEntry(
        directory=tc.dir_spelling(directory),
        command=line,
        file=tc.in_tree_space(src_path),
        output=tc.in_tree_space(obj_out),
        stem=tc.stem_of(src_path),
        language=('cpp' if src_path.suffix in ('.c', '.cpp', '.cc', '.cxx')
                  else 'fortran'),
    ), obj_out.resolve()


def _parse_archive(line: str, directory: Path) -> tuple[Path, list[str]] | None:
    """``ar crs <archive> <members...>`` as ``(archive, member tokens)``.

    Two spellings occur and both are read.  The common one names the archive
    POSITIONALLY — the first non-flag token — and ``-o`` is the unusual one;
    reading only ``-o`` yields an archive with no members, which links into
    nothing while looking complete.
    """
    try:
        argv = shlex.split(line)
    except ValueError:
        return None
    if Path(argv[0]).name == 'ranlib':
        return None
    if '-o' in argv:
        i = argv.index('-o')
        if i + 1 >= len(argv):
            return None
        out = Path(argv[i + 1])
        members = [t for t in argv[i + 2:] if not t.startswith('-')]
    else:
        nonflag = [t for t in argv[1:] if not t.startswith('-')]
        # `ar`'s traditional form writes the operation modifier WITHOUT a
        # leading dash — `ar crs lib.a a.o b.o` — so the archive is the
        # SECOND token there, while the dash form (`ar -crs lib.a ...`) puts
        # it first.  Told apart by whether the first token looks like an
        # archive, which is what keeps `crs` out of the member list; a
        # filter on the leading dash alone reads `crs` as the archive and
        # then names the real archive as a member, which resolves to no
        # known object and refuses the whole build.
        if nonflag and _is_archive(nonflag[0]):
            out, members = Path(nonflag[0]), nonflag[1:]
        elif len(nonflag) >= 2:
            out, members = Path(nonflag[1]), nonflag[2:]
        else:
            return None
    if not members:
        return None
    return ((out if out.is_absolute() else directory / out).resolve(), members)


def _synthetic_compile(argv: list[str], src: Path, obj: str) -> str:
    """A ``-c`` command for a source the build compiles inside a link line.

    Synthesised rather than reused from the link line.  A link line is not a
    compile: it names every object of the product, and reading flags out of
    it would carry all of them into the unit's compile options as bare paths,
    which the compiler then reports as "linker input file unused because
    linking not done".  The link line's FLAGS are the ones this source is
    compiled with — that is what makes it a link-line source rather than a
    separate compile — so they are kept and the structural part replaced.
    """
    keep: list[str] = []
    skip_next = False
    for i, token in enumerate(argv):
        if i == 0:
            continue
        if skip_next:
            skip_next = False
            continue
        if token == '-o':
            skip_next = True
            continue
        if _is_source(token) or _is_archive(token) or token.endswith('.o'):
            continue
        if token.startswith(('-Wl,', '-l')):
            continue                    # link-time, not compile-time
        keep.append(token)
    return shlex.join([argv[0], *keep, '-c', str(src), '-o', str(obj)])


def _link_sources(argv: list[str]) -> list[str]:
    """The source files a link line compiles as part of linking."""
    if '-o' not in argv:
        return []
    i = argv.index('-o')
    return [t for t in argv[i + 2:] if _is_source(t)]


def _resolve_objects(tc: Toolchain, directory: Path, tokens: list[str],
                     stem_of_object: dict[str, str]) -> list[str]:
    """Link tokens to the stems that produce them, keeping the ORDER.

    Order is semantic for static linking — ``ld`` resolves left to right, so a
    provider must come after its consumers — and a token naming no known
    object is REFUSED rather than dropped, because a silently shortened link
    line is a product that fails to link for a reason nothing points at.
    """
    out: list[str] = []
    for token in tokens:
        if _is_source(token) or token.startswith('-'):
            continue
        p = Path(token)
        absolute = str((p if p.is_absolute() else directory / p).resolve())
        stem = stem_of_object.get(absolute)
        if stem is None:
            raise ExtractionError(
                f'{directory} names {token}, which no compile in this build '
                f'produces.  The link line cannot be expressed as stems.')
        out.append(stem)
    return out


def _archive_line(directory: Path, archive: Path, members: list[str]) -> str:
    """The recorded ``ar`` command, re-spelled relative to *directory*.

    Reconstructed from the parsed parts because the dry run's line was already
    split; the members keep the ORDER the build gave them.
    """
    def rel(x: Path) -> str:
        try:
            return x.relative_to(directory).as_posix()
        except ValueError:
            return str(x)
    return ' '.join(['ar', 'crs', rel(archive), *(rel(Path(m) if
                     Path(m).is_absolute() else directory / m)
                     for m in members)])


def _parse_link(tc: Toolchain, directory: Path, argv: list[str],
                stem_of_object: dict[str, str],
                members_of_archive: dict[str, tuple[str, ...]]
                ) -> Product | None:
    """One link command as a :class:`Product`."""
    if '-o' not in argv:
        return None
    i = argv.index('-o')
    if i + 1 >= len(argv):
        return None
    out = Path(argv[i + 1])
    driver = Path(argv[0]).name
    objects: list[str] = []
    libs: list[str] = []
    for token in argv[i + 2:]:
        if token.startswith('-'):
            continue
        if _is_source(token):
            src = (Path(token) if Path(token).is_absolute()
                   else directory / token)
            objects.append(tc.stem_of(src))
            continue
        p = Path(token)
        absolute = str((p if p.is_absolute() else directory / p).resolve())
        if _is_archive(token):
            # Recorded as a LIBRARY, standing where the link line named it.
            #
            # Expanding it into its members was tried and is wrong: it is a
            # different link.  `ld` pulls only the members it needs out of an
            # archive, so a member nothing references stays out of the
            # product; listing every member as an input links them all.
            # Measured on a two-member archive whose second member is
            # unreferenced: the expanded link defined a symbol the project's
            # own executable does not have.
            #
            # Order is still preserved, which was the other reason for
            # expanding: the flat build names a library this generator also
            # produces by its target, and `target_link_libraries` keeps the
            # position the link line gave it.
            libs.append(tc.in_tree_space(Path(absolute)))
            continue
        stem = stem_of_object.get(absolute)
        if stem is None:
            raise ExtractionError(
                f'{directory} links {token}, which no compile in this build '
                f'produces.  The link line cannot be expressed as stems.')
        objects.append(stem)
    out_abs = (out if out.is_absolute() else directory / out)
    # The record: the EXACT link command and the directory it ran in, both
    # re-spelled against the tree — a replay runs them verbatim, and a
    # directory left absolute would make it write into the CAPTURE tree.
    return Product(output=tc.in_tree_space(out_abs), kind='exe',
                   stems=tuple(objects), libs=tuple(libs), driver=driver,
                   command=tc.in_tree_space_cmd(
                       _absolutise_paths(shlex.join(argv), str(directory)),
                       directory),
                   directory=tc.dir_spelling(directory))


def _refuse_missing_sources(entries: dict[str, CompileEntry]) -> None:
    """A unit whose source is absent cannot be built by a generated build.

    A Makefile that GENERATES its sources uses ``$(shell ...)``, which
    ``make -n`` does not expand, so those files exist on disk (the build made
    them) but no rule for them appears in the dry run.  A generated build
    would then look for a source nothing produces.  Detected here, where the
    gap can be named.
    """
    missing: list[str] = []
    for entry in entries.values():
        if entry.file.startswith(TREE):
            continue                    # an output this build writes
        if not Path(entry.file).exists():
            missing.append(entry.stem)
    if missing:
        raise ExtractionError(
            f'{len(missing)} unit(s) name a source that does not exist, which '
            f'`make -n` can show no rule for (a `$(shell ...)` generator '
            f'expands to nothing under `-n`).  First few: '
            f'{", ".join(sorted(missing)[:5])}.  The protocol would name '
            f'sources no generated build can produce.')

