"""A long recorded command is delivered as argv, never packed into one argument.

A single execve ARGUMENT may not exceed Linux's ``MAX_ARG_STRLEN`` (131072),
and the limit is PER ARGUMENT.  The flat build used to hand every recorded
command to a shell as ONE argument (``sh -c "<command>"``), so a command past
that length could not run at all: execve fails with E2BIG before the shell
starts.  Measured: ``sh -c`` with 65,536 bytes runs; at 131,071 it does not.

A big project reaches that.  Measured on a real corpus: its 1,965-object link
line is 126,616 bytes and its OWN build runs it through ``sh -c`` — four
thousand bytes under the wall, and only because that build spells its objects
RELATIVELY.  Spell the same objects under ``${TREE}`` and the line grows by
~20KB and dies.

The fix is not to shorten the spelling and not to hand the shell a file: it is
to stop PACKING the command into one argument.  A command delivered as separate
argv entries is bounded only by the TOTAL (about 2MB here) — which is exactly
what the native build does, and why the native build survives its own line.
Measured, above the per-argument limit:

    command ~152KB, one `sh -c` string  ->  E2BIG
    command ~152KB, separate arguments  ->  delivered
    command ~253KB, separate arguments  ->  delivered

So this file pins the DELIVERY, by running a generated build whose command is
past the limit, and asserting the command REACHES the toolchain instead of
dying at execve.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from protocolize.flat_cmake import flat_cmake
from protocolize.private_protocol import TREE, CompileEntry, Product, Protocol

pytestmark = pytest.mark.skipif(
    not Path('/bin/sh').exists(), reason='needs a POSIX shell')

#: Well past MAX_ARG_STRLEN (131072), so the delivery choice is unambiguous.
_TOO_LONG = 200_000

_PAD = 'obj/{i:05d}/a_rather_long_object_name_for_this_unit.o'


def _link(objs: list[str]) -> str:
    return f'gcc -o {TREE}/app ' + ' '.join(objs)


def _protocol(*, long: bool) -> Protocol:
    entries = [
        CompileEntry(directory=TREE, command=f'gcc -c -o u{i}.o {TREE}/u{i}.c',
                     file=f'{TREE}/u{i}.c', output=f'u{i}.o',
                     stem=f'u{i}', language='c')
        for i in range(2)
    ]
    objs = ['u0.o', 'u1.o']
    if long:
        one = _PAD.format(i=0)
        need = (_TOO_LONG - len(_link(objs))) // (len(one) + 1)
        objs += [_PAD.format(i=i) for i in range(need)]
    return Protocol(
        compilation=entries,
        products=[Product(output=f'{TREE}/app', kind='exe',
                          stems=('u0', 'u1'),
                          command=_link(objs), directory=TREE)],
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


def test_a_long_command_reaches_the_toolchain(tmp_path: Path):
    tree = _tree(tmp_path / 'tree')
    proto = _protocol(long=True)
    assert len(proto.products[0].command) > 131_072, (
        'the fixture must actually exceed MAX_ARG_STRLEN')

    flat_cmake(proto, tree=tree)
    r = _build(tree)
    combined = r.stdout + r.stderr

    # The link may fail because the padding objects do not exist — that is not
    # what this test is about.  What must NOT happen is the command being
    # undeliverable.
    assert 'Argument list too long' not in combined, combined
    assert 'cannot execute' not in combined, combined


def test_a_long_command_is_not_a_single_shell_argument(tmp_path: Path):
    """The command is emitted as argv, so no argument can hit the per-item cap."""
    tree = _tree(tmp_path / 'tree')
    proto = _protocol(long=True)
    flat_cmake(proto, tree=tree)

    text = (tree / 'CMakeLists.txt').read_text()
    # Find the link rule's COMMAND line and measure its largest argument.
    lines = [ln for ln in text.splitlines()
             if 'a_rather_long_object_name' in ln and ln.strip().startswith('COMMAND')]
    assert lines, 'the link command should be emitted as COMMAND'
    for ln in lines:
        argv = shlex.split(ln.strip()[len('COMMAND'):])
        biggest = max((len(a) for a in argv), default=0)
        assert biggest < 131_072, (
            f'an argument of {biggest} bytes would fail execve')
        assert len(argv) > 100, 'the objects should be SEPARATE arguments'


def test_a_shell_line_keeps_its_shell(tmp_path: Path):
    """A command with shell syntax still goes through a shell."""
    tree = _tree(tmp_path / 'tree')
    proto = _protocol(long=False)
    prod = proto.products[0]
    proto = type(proto)(compilation=proto.compilation, products=[
        type(prod)(output=prod.output, kind=prod.kind, stems=prod.stems,
                   command=f'cd {TREE} && gcc -o {TREE}/app u0.o u1.o '
                           f'&& echo done',
                   directory=TREE)], comment='')
    flat_cmake(proto, tree=tree)
    text = (tree / 'CMakeLists.txt').read_text()
    assert 'sh -c "' in text, (
        'a command carrying `&&` must keep its shell, or the operators become '
        'arguments to the first program')


def test_a_plain_command_still_builds(tmp_path: Path):
    tree = _tree(tmp_path / 'tree')
    flat_cmake(_protocol(long=False), tree=tree)
    r = _build(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tree / 'app').is_file()
