"""`atol` on a `field_assertion` is a DECLARED number, and it is read.

Measured before the fix: a plan naming `field_assertion` with `atol: 10.0` and
a row differing by 0.9 came back `FAIL (1 diffs)` — the key was accepted by the
schema (`problems()` only requires a `kind`) and then read by nobody.  The
comparator took its absolute floor from `model.near_zero`, so a plan could
declare a tolerance, get no complaint, and not have it applied.

The floor has to come from somewhere.  `model.near_zero` is the corpus saying
"a magnitude below this is a theoretical zero" — a statement about the MODEL.
`atol` is the plan saying "an UNRECOGNISED row may legitimately drift by this"
— a statement about the COMPARISON.  They are different claims about different
things, so they are different keys.

The scope is the point: `atol` reaches only the fallback — a row of a declared
table whose SHAPE matches no rule.  A declared column is still held to
`rtol`/`near_zero`, so a plan cannot set `atol` high and quietly stop checking
the physics it declared.
"""

from __future__ import annotations

import textwrap

from alcorpora.verify import compare

#: Three data rows under one declared table.  Row 1 matches the rule (3
#: tokens, first an int) and is compared field-wise; row 2 has five tokens,
#: which no rule claims, so it falls through to `numclose`.
_GEN = textwrap.dedent('''\
       DISPLACEMENTS
        1  1.000000E+00  0.0
        9  5.0  6.0  7.0  8.0
''')
_BASE = textwrap.dedent('''\
       DISPLACEMENTS
        1  1.000000E+00  0.0
        9  1.0  2.0  7.0  8.0
''')

_MODEL = {
    'tables': [
        {'titles': ['DISPLACEMENTS'],
         'rows': [{'match': {'first_int': True, 'n_tokens': 3},
                   'fields': {'indices': [1, 2]}}]},
    ],
}


def test_the_declared_atol_is_the_fallback_floor():
    """Row 2 differs by 4.0 per field, `atol=10.0` — an unrecognised row
    inside the declared tolerance is not a difference."""
    assert compare.field_lockstep(_GEN, _BASE, model=_MODEL, atol=10.0) == []


def test_without_atol_the_fallback_falls_back_to_near_zero():
    """The same pair, no `atol`: the floor is the model's `near_zero`
    (1e-3), the 4.0 drift is outside it, and the row differs — which is what
    every plan written before `atol` existed already relied on."""
    diffs = compare.field_lockstep(_GEN, _BASE, model=_MODEL)
    assert len(diffs) == 1, diffs
    assert diffs[0][0] == 2


def test_atol_does_not_excuse_a_declared_column():
    """Row 1 IS declared, so it is held to `rtol`/`near_zero`; a plan cannot
    widen `atol` and stop checking the physics it declared."""
    gen = _GEN.replace('1.000000E+00', '2.000000E+00')
    diffs = compare.field_lockstep(gen, _BASE, model=_MODEL, atol=1e6)
    assert len(diffs) == 1, diffs
    assert diffs[0][0] == 1
