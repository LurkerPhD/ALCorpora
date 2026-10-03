"""Parsing for standard ``compile_commands.json`` files.

The format is intentionally small: deepf2c only consumes the file list,
the compiler identity, include paths, integer/real default widths, and the
SAVE-related flag.  Everything else in a compiler command is ignored so a
project can keep its normal CMake/Ninja export without duplicating a
deepf2c-specific manifest.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from protocolize.tree_anchor import TREE, expand_tree

FORTRAN_EXTS = {'.f', '.f77', '.for', '.f90', '.f95', '.f03', '.f08',
                '.F', '.F77', '.FOR', '.F90', '.F95', '.F03', '.F08'}


@dataclass
class FileCompileFlags:
    """Compiler flags relevant to deepf2c conversion for one file."""
    compiler: str = "gfortran"
    int_width: int = 4
    real_width: int = 4
    fno_automatic: bool = False
    finit_local_zero: bool = False
    fixed_format: bool | None = None
    include_dirs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "compiler": self.compiler,
            "int_width": self.int_width,
            "real_width": self.real_width,
            "fno_automatic": self.fno_automatic,
            "finit_local_zero": self.finit_local_zero,
            "fixed_format": self.fixed_format,
            "include_dirs": list(self.include_dirs),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'FileCompileFlags':
        return cls(
            compiler=data.get('compiler', 'gfortran'),
            int_width=data.get('int_width', 4),
            real_width=data.get('real_width', 4),
            fno_automatic=bool(data.get('fno_automatic', False)),
            finit_local_zero=bool(data.get('finit_local_zero', False)),
            fixed_format=data.get('fixed_format'),
            include_dirs=list(data.get('include_dirs', [])),
        )


@dataclass
class CompileCommand:
    file: Path
    directory: Path
    flags: FileCompileFlags


def _compiler_from_argv0(argv0: str) -> str:
    base = Path(argv0).name.lower()
    if 'ifort' in base or 'ifx' in base:
        return 'ifort'
    if 'gfortran' in base:
        return 'gfortran'
    return 'gfortran'


def _tree_expand(text: str, tree_root: Path | str | None,
                 path: Path) -> str:
    """``${TREE}`` in *text* replaced by *tree_root*, or refused.

    The placeholder is the protocol's, and the value it stands for is the
    caller's to supply — the protocol cannot say it without pinning itself
    to one machine.  Refused rather than passed through when nobody says,
    because a leftover ``${TREE}`` is resolved as a RELATIVE path and
    produces a path that looks plausible and names nothing.
    """
    if TREE not in text:
        return text
    if tree_root is None:
        raise ValueError(
            f'{path.name} spells a path with {TREE}, which is the private '
            f'protocol\'s placeholder for the tree it was captured from; '
            f'say which tree that is (tree_root=...) before reading it as a '
            f'compile database — resolving it as a relative path produces a '
            f'path that names nothing')
    return expand_tree(text, tree_root)


def _entries_of(data: object) -> list:
    """The compile entries, from either shape this reader accepts.

    The standard format is a bare array.  The private protocol is an object
    whose ``compilation`` holds that same array — it is a SUPERSET of this
    format, so pointing the transpiler at a protocol must work by reading
    this field and ignoring the rest, with no conversion step in between.

    Taking both here rather than at each call site is what keeps ONE reader:
    when the front end normalized and this function did not, the discover
    step re-parsed the raw file and died on the object's keys.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        comp = data.get('compilation')
        if isinstance(comp, list):
            return comp
        raise ValueError(
            "a JSON object with no 'compilation' array is neither a compile "
            "database nor a protocol")
    raise ValueError(f'expected a compile database or a protocol, '
                     f'got {type(data).__name__}')


def parse_compile_commands(path: Path, *,
                           tree_root: Path | str | None = None,
                           ) -> list[CompileCommand]:
    """Parse a compile database OR a protocol; return only Fortran entries.

    *tree_root* is what ``${TREE}`` means.  A protocol spells a path under
    the tree it was captured from with that placeholder — which is what
    makes it replayable somewhere else — and the standard format has no
    such spelling, so a reader that has never seen it resolves the operand
    against the entry's ``directory`` instead:

        file      ``${TREE}/BLAS/SRC/.../isamax_64.f``
        directory ``${TREE}/.``
        -> ``<capture>/build_lapack/${TREE}/${TREE}/BLAS/.../isamax_64.f``

    Measured on the real corpora: LAPACK 3489 of 6794 entries, MYSTRAN 570
    files and 11282 include directories named paths that do not exist — and
    a scan that finds nothing reports a clean, empty corpus rather than an
    error.  A caller that does not say what the placeholder means therefore
    gets a REFUSAL, not a resolution: there is no right answer to guess.
    """
    raw = _entries_of(json.loads(path.read_text(encoding='utf-8')))
    commands: list[CompileCommand] = []
    for entry in raw:
        file_text = entry.get('file')
        if not file_text:
            continue
        directory = Path(_tree_expand(entry.get('directory') or '.',
                                      tree_root, path))
        if not directory.is_absolute():
            directory = path.resolve().parent / directory
        file = Path(_tree_expand(file_text, tree_root, path))
        if not file.is_absolute():
            file = directory / file
        if file.suffix not in FORTRAN_EXTS:
            continue

        args = entry.get('arguments')
        if args is None:
            command = entry.get('command')
            if not command:
                continue
            args = shlex.split(command)
        else:
            args = list(args)

        flags = FileCompileFlags()
        if args:
            flags.compiler = _compiler_from_argv0(str(args[0]))
        # Compiler flags are scanned in order; later flags win, matching
        # gfortran/ifort command-line semantics for repeated options.
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ('-i8', '-fdefault-integer-8'):
                flags.int_width = 8
            elif arg in ('-i4', '-fdefault-integer-4'):
                flags.int_width = 4
            elif arg in ('-r8', '-fdefault-real-8'):
                flags.real_width = 8
            elif arg in ('-r4', '-fdefault-real-4'):
                flags.real_width = 4
            elif arg in ('-fno-automatic', '-save'):
                flags.fno_automatic = True
            elif arg in ('-fautomatic'):
                flags.fno_automatic = False
            elif arg == '-finit-local-zero':
                flags.finit_local_zero = True
            elif arg in ('-ffixed-form', '-fixed'):
                flags.fixed_format = True
            elif arg in ('-ffree-form', '-free'):
                flags.fixed_format = False
            elif arg.startswith('-I') and len(arg) > 2:
                inc = Path(_tree_expand(arg[2:], tree_root, path))
                flags.include_dirs.append(
                    str(inc if inc.is_absolute() else directory / inc))
            elif arg == '-I' and i + 1 < len(args):
                i += 1
                inc = Path(_tree_expand(args[i], tree_root, path))
                flags.include_dirs.append(
                    str(inc if inc.is_absolute() else directory / inc))
            i += 1

        commands.append(CompileCommand(file=file.resolve(),
                                      directory=directory.resolve(),
                                      flags=flags))
    return commands


def compile_command_map(path: Path) -> dict[str, FileCompileFlags]:
    """Return ``{absolute source path: FileCompileFlags}``."""
    return {str(c.file): c.flags for c in parse_compile_commands(path)}
