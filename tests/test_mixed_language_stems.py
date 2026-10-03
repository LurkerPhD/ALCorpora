"""One stem may be TWO units: a Fortran module and the C it binds.

Measured on a real project (a Fortran library with a C implementation layer):
`stdlib_system.F90` and `stdlib_system.c` are distinct translation units with
the same stem — the module binds the C function with `bind(c)`. The stem is
still the unit's IDENTITY (a product names its members by stem), but it is not
a unique KEY, and treating it as one silently drops a unit or refuses a legal
project.
"""

from __future__ import annotations

import json

from protocolize.flat_cmake import flat_cmake, select
from protocolize.private_protocol import CompileEntry, Protocol


def _mixed() -> Protocol:
    return Protocol(compilation=[
        CompileEntry(directory='/t', command='/usr/bin/gfortran -c /t/a.F90 -o a.f90.o',
                     file='/t/a.F90', output='a.f90.o', stem='a',
                     language='fortran', provides=('m',)),
        CompileEntry(directory='/t', command='/usr/bin/gcc -c /t/a.c -o a.c.o',
                     file='/t/a.c', output='a.c.o', stem='a', language='cpp'),
    ])


def test_one_stem_may_hold_two_languages():
    assert _mixed().problems() == []


def test_the_same_language_twice_is_still_refused():
    p = Protocol(compilation=[
        CompileEntry(directory='/t', command='gcc -c /t/a.c -o a.o',
                     file='/t/a.c', output='a.o', stem='a', language='cpp'),
        CompileEntry(directory='/t', command='gcc -c /t/b.c -o b.o',
                     file='/t/b.c', output='b.o', stem='a', language='cpp'),
    ])
    assert any('appears twice' in x for x in p.problems())


def test_select_keeps_both_units(tmp_path):
    sel = select(_mixed())
    assert len(sel.entries) == 2


def test_the_generated_build_compiles_both(tmp_path):
    (tmp_path / 'a.F90').write_text('x')
    flat_cmake(_mixed(), tree=tmp_path)
    text = (tmp_path / 'CMakeLists.txt').read_text(encoding='utf-8')
    assert 'a.F90' in text and 'a.c' in text
    assert text.count('add_custom_command(OUTPUT') == 2
