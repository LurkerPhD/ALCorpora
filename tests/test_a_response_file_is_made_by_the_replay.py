"""A response file is this build's scratch, and a replay must make it.

`ar qc lib.a @CMakeFiles/lapack.rsp` names a file the build generator writes
just before the edge runs and removes after.  It is declared NOWHERE — not as
an output of any edge — so two separate things went wrong, and the second is
the one that hid:

* the `@` is the program's argument syntax, not part of the path.  Treated as
  one, `@CMakeFiles/lapack.rsp` was spelled `<build>/@CMakeFiles/lapack.rsp` —
  the prefix glued on, a path that exists nowhere.
* without `@`, the path was still spelled ABSOLUTE, pointing a replay at the
  CAPTURE tree.  Nothing declares a response file as an output, so the
  `produced` set cannot see it and it is classified as "a file the project
  ships under the build tree" — the same class as a hand-written file that
  happens to live there.

Measured on a real corpus whose archive edge alone used one: the replay
compiled 6,794 units, archived every other library, and died on the one with
the response file — `/usr/bin/ar: <capture>/@CMakeFiles/lapack.rsp: No such
file or directory`.

The file's CONTENT is not recorded and does not need to be: for an archive
edge it is the product's inputs, one per line (`$in_newline` in the
generator's own words), which the protocol already carries as `stems`.
"""

from __future__ import annotations

from pathlib import Path

from alcorpora.flat_cmake import flat_cmake
from alcorpora.private_protocol import (TREE, CompileEntry, Product,
                                          Protocol)

_RSP = 'CMakeFiles/lapack.rsp'


def _protocol() -> Protocol:
    """An archive whose command reads a response file."""
    return Protocol(
        compilation=[
            CompileEntry(directory=f'{TREE}/build',
                         command=f'gcc -c -o o{i}.o {TREE}/src/s{i}.c',
                         file=f'{TREE}/src/s{i}.c', output=f'o{i}.o',
                         stem=f's{i}', language='c')
            for i in range(3)
        ],
        products=[Product(
            output=f'{TREE}/build/lib/libx.a', kind='archive',
            stems=('s0', 's1', 's2'),
            command=(f'cd {TREE}/build && /usr/bin/ar qc lib/libx.a '
                     f'@{_RSP}'),
            directory=f'{TREE}/build',
            rspfiles=(_RSP,),
        )],
        comment='response-file fixture')


def test_a_product_records_the_response_file_it_reads(tmp_path: Path):
    """The fact is recorded, because it cannot be re-derived: nothing
    declares a response file as an output, so it is indistinguishable from a
    file the project ships."""
    proto = _protocol()
    assert proto.products[0].rspfiles == (_RSP,)


def test_the_recorded_spelling_survives_a_round_trip(tmp_path: Path):
    path = tmp_path / 'protocol.json'
    _protocol().save(path)
    back = Protocol.load(path)
    assert back.products[0].rspfiles == (_RSP,)


def test_flat_cmake_materialises_the_response_file(tmp_path: Path):
    """Written where the command will look for it — beside its own directory."""
    tree = tmp_path / 'tree'
    tree.mkdir()
    flat_cmake(_protocol(), tree=tree)

    rsp = tree / 'build' / _RSP
    assert rsp.is_file(), (
        'a replay that does not write the response file archives against a '
        'path that exists in neither tree')

    # Its content is the product's inputs, one per line — the generator's own
    # `$in_newline`.
    lines = [ln for ln in rsp.read_text().splitlines() if ln.strip()]
    assert len(lines) == 3, lines
    assert all(ln.endswith('.o') for ln in lines), lines


def test_the_rsp_path_is_rooted_in_the_replay_tree(tmp_path: Path):
    """Not the capture tree: `${TREE}` travels, an absolute path does not."""
    tree = tmp_path / 'tree'
    tree.mkdir()
    flat_cmake(_protocol(), tree=tree)
    text = (tree / 'CMakeLists.txt').read_text()
    assert str(tree) in text, 'the rsp must be spelled under the replay tree'
    assert '${TREE}' not in text, 'placeholders must be expanded in the output'


def test_the_at_operand_is_rerooted_not_treated_as_a_path(tmp_path: Path):
    """The `@` is the program's syntax; the PATH after it is what gets rooted.

    Without this the token is read as one relative path and the prefix is
    glued on: `<build>/@CMakeFiles/lapack.rsp`, which exists nowhere — and
    then, being under the tree but not "produced", it is spelled ABSOLUTE
    and points at the capture tree as well.
    """
    from alcorpora.onboard.extract import Toolchain

    root = tmp_path / 'proj'
    (root / 'build').mkdir(parents=True)
    tc = Toolchain(build=root / 'build', root=root)
    tc.produced = frozenset()

    spelled = tc.in_tree_space_cmd('ar qc lib.a @CMakeFiles/lapack.rsp',
                                   root / 'build')
    assert '@${TREE}/build/CMakeFiles/lapack.rsp' in spelled, spelled
    assert '@' + str(root) not in spelled, (
        'the path after @ must be re-rooted under ${TREE}, not left absolute')
    assert '/@' not in spelled, (
        'the @ must not be glued onto the path')
