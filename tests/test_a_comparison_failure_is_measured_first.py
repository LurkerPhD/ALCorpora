"""A comparison failure is MEASURED before an agent is asked.

The lines that differ between two runs of one binary ARE the volatile lines,
and that is a deterministic measurement, not a judgement — `alcorpora
.volatiles` has owned it all along and nothing in the admission flow called
it.  So a `B-project` failure now measures first (two runs, the second under
a shifted TZ), turns each moved shape into a `drop` rule, and asks the seam
ONLY for what the measurement cannot reach.
"""

from __future__ import annotations

import re

from alcorpora import volatiles


def test_a_measured_shape_becomes_a_rule_that_matches_the_real_lines():
    """The rule is built from the SHAPE, so it must match a line with any
    number in the shape's place — and wider whitespace, because a shape
    collapses runs a printed table pads."""
    pat = volatiles.drop_pattern('END TIME: #: #:#')
    assert re.search(pat, ' END TIME: 18: 8:19')
    # Same SHAPE, wider padding: a printed field pads to its number's width,
    # so the rule must flex the runs or it matches nothing on the next deck.
    assert re.search(pat, ' END TIME:   18:  8:19'), 'wider runs must match'
    assert volatiles.shape_of(' END TIME: 18: 8:19') == \
        volatiles.shape_of(' END TIME:   18:  8:19'), (
            'padding is not part of the sentence: both are one shape')
    # And the rule built from the shape matches BOTH spellings, because the
    # number matcher takes the whitespace around it.
    loose = volatiles.drop_pattern(volatiles.shape_of(' END TIME: 18: 8:19'))
    assert re.search(loose, ' END TIME:   18:  8:19')
    assert re.search(loose, ' END TIME: 18: 8:19')


def test_padding_at_a_boundary_is_not_a_second_shape():
    """Two runs of one program print `at 20:37:16.314` and `at 20:30: 3.493`.

    The padding before the `3` is a column's, not the sentence's — but a mask
    that replaces only the digits leaves it in the shape, so ONE volatile line
    looks like TWO and the rule built from either misses the other.  Measured
    on a real corpus, where this produced forty rules that matched nothing.
    """
    a = ' >> MYSTRAN END    : 10/ 4/2026 at 20:37:16.314'
    b = ' >> MYSTRAN END    : 10/ 4/2026 at 20:30: 3.493'
    assert volatiles.shape_of(a) == volatiles.shape_of(b), (
        'the numbers differ in width; the shape must not')
    pat = volatiles.drop_pattern(volatiles.shape_of(a))
    assert re.search(pat, a) and re.search(pat, b)


def test_one_rule_covers_two_different_values_of_one_mechanism():
    """A shape is the mechanism, not the value: two runs that printed
    different numbers in the same sentence are one rule."""
    pat = volatiles.drop_pattern(volatiles.shape_of(' B =    21 BBAR = 100'))
    assert re.search(pat, ' B =     4 BBAR =   3')


def test_a_rule_does_not_swallow_a_longer_line():
    """Anchored, so it drops what the shape names and not its prefixes."""
    pat = volatiles.drop_pattern('END TIME: #: #:#')
    assert not re.search(pat, ' END TIME: 18: 8:19 EXTRA')


def test_literal_text_is_escaped_not_interpreted(tmp_path):
    """A shape's text is TEXT: a `+` in it must not become a quantifier."""
    pat = volatiles.drop_pattern('TOTAL (+) # ITEMS')
    assert re.search(pat, ' TOTAL (+) 12 ITEMS')
    assert not re.search(pat, ' TOTAL 12 ITEMS'), (
        'the literal " (+)" must be required, not treated as a group')
