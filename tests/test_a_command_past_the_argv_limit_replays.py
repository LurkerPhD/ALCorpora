"""A command too long to be an argv is still replayed — through a script file.

A single execve ARGUMENT may not exceed Linux's ``MAX_ARG_STRLEN`` (131072).
The flat build delivered every recorded command as ONE argument
(``sh -c "<command>"``), so a command past that length could not be run at all:
execve fails with E2BIG before the shell starts.  Measured on this machine:
``sh -c`` with 65,536 bytes runs; at 131,071 it does not.

This is not avoidable by a big project.  A link line grows with the number of
units, and one real corpus's executable link is 244,887 bytes once every object
is spelled absolutely — while the SAME build's own ninja line is 126,616 bytes,
just under the limit, because it names objects RELATIVELY.  So the project
builds and its replay does not, and the failure reads as "the protocol does not
rebuild the project".

The fix changes only HOW the shell is reached: a short command still goes in as
an argument, an oversized one is written to a file and run with ``sh <file>``.
Nothing is parsed, split or re-quoted, so the replay stays VERBATIM.

An earlier version of this test padded the command with repeated ``-D`` flags
and failed even after the fix — ``gcc`` re-execs ``collect2`` and dies on the
same class of limit for that shape.  That was the TEST's shape being wrong, not
the fix: measured, the real shape (many short object paths totalling ~1 MB)
runs fine through a script file.  The fixture below therefore uses the real
shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from protocolize.flat_cmake import flat_cmake
from protocolize.private_protocol import TREE, CompileEntry, Product, Protocol

pytestmark = pytest.mark.skipif(
    not Path('/bin/sh').exists(), reason='needs a POSIX shell')

#: Past MAX_ARG_STRLEN (131072) with margin, so the delivery choice is
#: unambiguous rather than sitting on the boundary.
_TOO_LONG = 200_000

#: The real shape: distinct object paths, as a large link names them.  The
#: padding is computed from the ACTUAL spelling, not an estimate of it.
_PAD_OBJ = 'obj/{i:05d}/a_rather_long_object_name_for_this_unit.o'


def link_of(objs: list[str]) -> str:
    return f'gcc -o {TREE}/app ' + ' '.join(objs)


def _protocol(*, long: bool) -> Protocol:
    """A link command of the REAL shape, long or short."""
    units = 2
    entries = [
        CompileEntry(directory=TREE, command=f'gcc -c -o u{i}.o {TREE}/u{i}.c',
                     file=f'{TREE}/u{i}.c', output=f'u{i}.o',
                     stem=f'u{i}', language='c')
        for i in range(units)
    ]
    objs = [f'u{i}.o' for i in range(units)]
    if long:
        one = _PAD_OBJ.format(i=0)
        need = (_TOO_LONG - len(link_of(objs))) // (len(one) + 1)
        objs += [_PAD_OBJ.format(i=i) for i in range(need)]
    link = link_of(objs)
    return Protocol(
        compilation=entries,
        products=[Product(output=f'{TREE}/app', kind='exe',
                          stems=tuple(f'u{i}' for i in range(units)),
                          command=link, directory=TREE)],
        comment='argv-limit fixture')


def _tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'u0.c').write_text('int main(void) { return 0; }\n')
    (root / 'u1.c').write_text('int u1(void) { return 1; }\n')
    return root


def _build(tree: Path) -> subprocess.CompletedProcess:
    r = subprocess.run(['cmake', '-S', str(tree), '-B', str(tree / 'b'),
                        '-G', 'Ninja'], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return subprocess.run(['cmake', '--build', str(tree / 'b')],
                          capture_output=True, text=True)


def test_a_link_command_past_the_argv_limit_is_delivered(tmp_path: Path):
    """The command REACHES the toolchain instead of dying at execve."""
    tree = _tree(tmp_path / 'tree')
    proto = _protocol(long=True)
    assert len(proto.products[0].command) > 131_072, (
        'the fixture must actually exceed MAX_ARG_STRLEN')

    flat_cmake(proto, tree=tree)
    r = _build(tree)
    combined = r.stdout + r.stderr

    # The link may fail because the padding objects do not exist — that is
    # not what this test is about.  What must NOT happen is the command being
    # undeliverable.
    assert 'Argument list too long' not in combined, combined
    assert 'cannot execute' not in combined, combined

    scripts = sorted((tree / '_replay_cmds').glob('*.sh'))
    assert scripts, 'an oversized command must be written to a script'


def test_the_script_carries_the_recorded_command_verbatim(tmp_path: Path):
    """Delivered through a file, the TEXT is the same — no re-quoting."""
    tree = _tree(tmp_path / 'tree')
    proto = _protocol(long=True)
    flat_cmake(proto, tree=tree)

    scripts = sorted((tree / '_replay_cmds').glob('*.sh'))
    written = scripts[0].read_text()
    expected = proto.products[0].command.replace(TREE, str(tree))
    assert expected in written, (
        'the recorded command must appear verbatim, not re-quoted or split')


def test_a_short_command_is_still_one_argument(tmp_path: Path):
    """The fix must not change how an ordinary command is delivered."""
    tree = _tree(tmp_path / 'tree')
    proto = _protocol(long=False)
    flat_cmake(proto, tree=tree)
    text = (tree / 'CMakeLists.txt').read_text()
    assert 'sh -c "' in text, 'a short command should stay an -c argument'
    assert not (tree / '_replay_cmds').exists(), (
        'no script file is needed for a command that fits')


def test_a_short_command_still_builds(tmp_path: Path):
    """The end-to-end path is unchanged for the ordinary case."""
    tree = _tree(tmp_path / 'tree')
    flat_cmake(_protocol(long=False), tree=tree)
    r = _build(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tree / 'app').is_file()
