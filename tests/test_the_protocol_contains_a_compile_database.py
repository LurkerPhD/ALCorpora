"""The build protocol CONTAINS a compile database; it is not one.

The earlier constraint was that a protocol file must be a bare
``compile_commands.json`` array, on the theory that a cc consumer ignores an
element it does not recognise.  Measured, that theory is false against the
consumers that exist:

    clangd 18.1.3, --check against a probe source

      entry {directory, file, command}              exit=0  loaded
      entry + "stem"                                exit=3  Unknown key: "stem"
      entry + "language"                            exit=3  Unknown key: "language"
      entry + "output"                              exit=3  Unknown key: "output"
      array with one non-object element             exit=3  Expected object.

A compile-database loader is a strict WHITELIST and ALL-OR-NOTHING: one entry
carrying a field cc does not define makes it reject the ENTIRE file, so the
clean entries beside it become unreadable too.  `clang-check` agrees (exit 1,
"Error while trying to load a compilation database").

So the protocol does not pretend.  On disk it is an OBJECT whose
``compile_commands`` element holds the entries — entries that carry the
protocol's own fields (`stem`, `language`, `output`), because those are how a
consumer resolves a link member to a unit.  A plain compile database is
available as an EXPORT (`to_cc`), where every non-cc field is dropped.

What this file pins is that both halves stay true: the stored form is the
protocol, the exported form is valid cc, and neither leaks into the other.
"""

from __future__ import annotations

import json

import pytest

from alcorpora.private_protocol import (CompileEntry, Product, Protocol)

_CC_KEYS = {'directory', 'file', 'command', 'arguments'}


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
                          stems=('a', 'b'))],
    )


def test_the_file_is_an_object_containing_compile_commands(tmp_path):
    path = tmp_path / 'protocol.json'
    _proto().save(path)
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert isinstance(raw, dict), (
        'the storage form is the protocol object, not a bare array')
    assert isinstance(raw['compile_commands'], list)
    assert len(raw['compile_commands']) == 2
    assert raw['products'], 'the sections ride beside the entries'


def test_the_stored_entries_keep_the_protocols_own_fields(tmp_path):
    """Stored entries are NOT reduced to cc: a consumer needs `stem`."""
    path = tmp_path / 'protocol.json'
    _proto().save(path)
    entries = json.loads(path.read_text(encoding='utf-8'))['compile_commands']
    assert [e['stem'] for e in entries] == ['a', 'b']
    assert all('language' in e for e in entries)


def test_to_cc_drops_every_field_cc_does_not_define(tmp_path):
    """The EXPORT is valid cc — measured, because a loader is a whitelist."""
    for entry in _proto().to_cc():
        assert set(entry) <= _CC_KEYS, (
            f'{set(entry) - _CC_KEYS} would make a cc loader reject the '
            f'WHOLE file, not just this entry')
        for field in ('directory', 'file', 'command'):
            assert entry.get(field), f'{field} missing from the export'
        assert entry['command'].startswith('gcc ')


def test_the_export_is_a_bare_array(tmp_path):
    """`to_cc` is what a cc consumer is handed, and it is an array."""
    cc = _proto().to_cc()
    assert isinstance(cc, list)
    assert all(isinstance(e, dict) for e in cc)


def test_a_saved_protocol_round_trips(tmp_path):
    path = tmp_path / 'protocol.json'
    proto = _proto()
    proto.save(path)
    back = Protocol.load(path)
    assert [e.stem for e in back.compilation] == ['a', 'b']
    assert [p.output for p in back.products] == ['/b/libx.a']
    assert back.problems() == []


def test_the_older_array_shape_still_loads(tmp_path):
    """A protocol captured before the change must not need re-admitting."""
    path = tmp_path / 'protocol.json'
    node = {k: v for k, v in _proto().to_json().items()
            if k != 'compile_commands'}
    old = [e.to_json() for e in _proto().compilation] + [node]
    path.write_text(json.dumps(old), encoding='utf-8')
    back = Protocol.load(path)
    assert [e.stem for e in back.compilation] == ['a', 'b']
    assert back.products, 'the node carried the sections'


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


def test_a_product_merged_its_two_names_for_one_relation():
    """`members` and `objects` were one relation under two names.

    Which one was filled in was decided by `kind` and nothing else — an
    archive's members and an executable's own units are both "these units go
    into this product".  A protocol is an interface, so the duplicate name is
    a tax on every consumer: two spellings of one fact, and no way to know
    which to write.  The field is `stems` now — the value's own name, since
    each entry is a `CompileEntry.stem`.

    Both older spellings still READ, so a protocol written before the merge
    loads without being re-admitted.  Nothing writes them.
    """
    old_archive = {'output': '${TREE}/lib/a.a', 'kind': 'archive',
                   'members': ['SRC/x', 'SRC/y']}
    old_exe = {'output': '${TREE}/bin/a', 'kind': 'exe',
               'objects': ['SRC/z'], 'libs': ['${TREE}/lib/a.a']}

    assert Product.from_json(old_archive).stems == ('SRC/x', 'SRC/y')
    assert Product.from_json(old_exe).stems == ('SRC/z',)

    for written in (Product.from_json(old_archive).to_json(),
                    Product.from_json(old_exe).to_json()):
        assert 'stems' in written
        assert 'members' not in written and 'objects' not in written
