"""The build protocol IS a compile_commands.json, not merely like one.

The constraint this pins: a build protocol must be **directly usable as a
compile database**.  So the file on disk is a JSON ARRAY whose elements are
compile entries, and a cc-only consumer reads it without unwrapping anything.
Extra entries are permitted; the array structure is not disturbed.

The protocol's own sections (products, generated inputs, prepare, tests, ...)
ride in ONE final node — a compile entry never carries ``protocol_version``,
so the node is unambiguous, and a cc consumer skips an element with no
``file``/``command``.
"""

from __future__ import annotations

import json

from protocolize.private_protocol import (CompileEntry, Product, Protocol,
                                          _NODE_KEY)

_CC_FIELDS = ('directory', 'command', 'file', 'output')


def _proto() -> Protocol:
    return Protocol(
        compilation=[
            CompileEntry(directory='/b', command='gcc -c /s/a.c -o a.o',
                         file='/s/a.c', output='a.o', stem='a', language='cpp'),
            CompileEntry(directory='/b', command='gcc -c /s/b.c -o b.o',
                         file='/s/b.c', output='b.o', stem='b', language='cpp',
                         provides=('m',), uses=('m',)),
        ],
        products=[Product(output='/b/libx.a', kind='archive',
                          members=('a', 'b'))],
    )


def test_the_file_is_a_json_array(tmp_path):
    path = tmp_path / 'protocol.json'
    _proto().save(path)
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert isinstance(raw, list), (
        'the protocol file must be a bare array so a cc consumer can read it')


def test_every_compile_entry_is_a_cc_entry(tmp_path):
    """The elements before the node carry the four standard fields intact."""
    path = tmp_path / 'protocol.json'
    _proto().save(path)
    raw = json.loads(path.read_text(encoding='utf-8'))
    entries = [e for e in raw if _NODE_KEY not in e]
    assert len(entries) == 2
    for e in entries:
        for field in _CC_FIELDS:
            assert e.get(field), f'{field} missing from a compile entry: {e}'


def test_the_sections_are_an_extra_node_and_do_not_break_cc(tmp_path):
    path = tmp_path / 'protocol.json'
    _proto().save(path)
    raw = json.loads(path.read_text(encoding='utf-8'))
    nodes = [e for e in raw if _NODE_KEY in e]
    assert len(nodes) == 1, 'exactly one node carries the protocol sections'
    node = nodes[-1]
    assert node['products'], 'the sections survive in the node'
    # A cc-only consumer takes the entries and skips the node WITHOUT missing
    # a required field, which is what makes the array directly usable.
    cc = [e for e in raw if all(f in e for f in _CC_FIELDS)]
    assert len(cc) == 2


def test_a_saved_protocol_round_trips(tmp_path):
    path = tmp_path / 'protocol.json'
    proto = _proto()
    proto.save(path)
    back = Protocol.load(path)
    assert [e.stem for e in back.compilation] == ['a', 'b']
    assert [p.output for p in back.products] == ['/b/libx.a']
    assert back.problems() == []


def test_a_plain_compile_database_is_still_the_weak_form(tmp_path):
    """An array with no node is a compile database, and reads as one."""
    path = tmp_path / 'compile_commands.json'
    path.write_text(json.dumps([
        {'directory': '/b', 'command': 'gcc -c /s/a.c -o a.o',
         'file': '/s/a.c', 'output': 'a.o'},
    ]), encoding='utf-8')
    proto = Protocol.load(path)
    assert len(proto.compilation) == 1
    assert proto.compilation[0].file == '/s/a.c'
    # The compilation half is exact; the products half is empty, so a
    # consumer that needs a link line fails loudly rather than inventing one.
    assert proto.products == []
