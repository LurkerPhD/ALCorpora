"""Comparing a produced artifact against a golden — with the rules passed IN.

Three copies of this lived in the repo: the NASTRAN and MYSTRAN benchmark
programs and ``the reference-diff tool``.  The first two differed only in
their volatile-line lists; the algorithm — ``_numclose`` and the lockstep
loop — was character-identical between them, and the third said so in its
docstring, which is how a copy announces itself.  Those programs are gone;
this module and its tests are what remains of them.

So the algorithm lives here once, and the two things that are genuinely a
corpus's own — which lines are volatile, and how much numeric slack the
physics allows — arrive as arguments.  That is the entire difference this
mechanism is for: not a better comparator, but one that does not carry one
corpus's numbers inside it.

THIS IS A MOVE, NOT A REWRITE, and three details are load-bearing enough that
``tests/unit/test_the_comparison_matches_the_scripts_it_replaces.py`` checks
them against the original functions themselves:

* the volatile patterns are REGEXES, matched with ``.search``.  One of
  NASTRAN's is ``^\\d`` — "a line starting with a digit is not compared" —
  which no substring test can express;
* the whitespace collapse does not strip.  ``  A 1.0`` becomes `` A 1.0``
  with its leading space kept, so a line that lost its indentation still
  differs from one that kept it;
* the number pattern takes ``E`` exponents only.  Accepting ``D`` as well
  would change how many numbers a Fed-Fortran line is seen to contain, which
  decides the length check before any value is compared.
"""

from __future__ import annotations

import re

#: Numbers, exactly as the corpora's own scripts spell them: ``1``, ``1.5``,
#: ``.5``, ``1.5E-3``.  ``E`` only — see the module docstring.
_FLOAT_RE = re.compile(r'[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?|[-+]?\d*\.?\d+')

#: What a number is replaced by when checking that two lines have the same
#: SHAPE.  ``#`` cannot collide with a number's own text.
_MASK = '#'

DEFAULT_RTOL = 0.05
DEFAULT_ATOL = 0.5


def normalize(text: str, drop) -> list[str]:
    """The comparable form of *text*: volatile lines gone, whitespace folded.

    *drop* is an iterable of REGEX SOURCE STRINGS; a line that any of them
    matches is not compared.  Patterns rather than plain text because that is
    what a corpus declares — NASTRAN drops every line starting with a digit,
    and a search for the literal ``'^\\d'`` would drop nothing at all while
    looking entirely reasonable.

    Blank lines disappear and runs of whitespace fold to one space, but
    leading whitespace is NOT stripped: the corpora do not strip it, and
    stripping here would make a line whose indentation changed compare equal
    to one whose indentation did not.
    """
    patterns = [re.compile(p) for p in drop if p]
    kept: list[str] = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s.strip():
            continue
        if any(p.search(s) for p in patterns):
            continue
        kept.append(re.sub(r'\s+', ' ', s))
    return kept


def numclose(gen_line: str, base_line: str, *, rtol: float = DEFAULT_RTOL,
             atol: float = DEFAULT_ATOL) -> bool:
    """Whether two comparable lines agree, numerically or exactly.

    The order of the three tests is the whole semantics:

    1. byte equality — the common case, and the only one a non-numeric line
       can pass;
    2. **every** number pair within ``atol`` (absolute) or ``rtol`` (relative
       to the larger magnitude).  ``atol`` is tried first and independently,
       which is what lets a value that is physically zero wobble around zero:
       a ratio test alone rejects two near-zero numbers that differ wildly in
       ratio and not at all in magnitude, and that is the reference
       toolchain's own noise;
    3. the same line with every number masked, so a difference in the
       NON-numeric text still fails even when the numbers line up.
    """
    if gen_line == base_line:
        return True
    gn = [float(m) for m in _FLOAT_RE.findall(gen_line)]
    bn = [float(m) for m in _FLOAT_RE.findall(base_line)]
    if len(gn) != len(bn) or not gn:
        # No numbers, or a different count: only the exact test above could
        # have matched, so this is a difference in shape.
        return False
    for g, b in zip(gn, bn):
        if g == b:
            continue
        d = abs(g - b)
        if d <= atol:
            continue
        if d / max(abs(b), abs(g), 1e-30) > rtol:
            return False
    return _FLOAT_RE.sub(_MASK, gen_line) == _FLOAT_RE.sub(_MASK, base_line)


def lockstep(gen_text: str, base_text: str, *, drop,
             rtol: float = DEFAULT_RTOL, atol: float = DEFAULT_ATOL,
             max_diffs: int | None = None) -> list[tuple]:
    """Compare two artifacts line for line.

    Returns ``(lineno, produced, golden)`` for each disagreement, with the
    literal ``'(missing)'`` on the side that ran short — which side is short
    is the first thing anyone diagnosing this needs, so it is in the data
    rather than inferred from a count.

    *max_diffs* caps the report the way NASTRAN's own script does, with a
    final ``(None, '...', '(<n> more lines)')`` row, so a file that diverged
    everywhere reports the place it started diverging rather than thousands
    of lines.
    """
    gl = normalize(gen_text, drop)
    bl = normalize(base_text, drop)
    diffs: list[tuple] = []
    for i in range(max(len(gl), len(bl))):
        if i >= len(gl):
            diffs.append((i, '(missing)', bl[i]))
        elif i >= len(bl):
            diffs.append((i, gl[i], '(missing)'))
        elif gl[i] != bl[i] and not numclose(gl[i], bl[i], rtol=rtol, atol=atol):
            diffs.append((i, gl[i], bl[i]))
        if max_diffs is not None and len(diffs) >= max_diffs:
            more = max(len(gl), len(bl)) - i - 1
            if more > 0:
                diffs.append((None, '...', f'({more} more lines)'))
            break
    return diffs


# ── a corpus's per-table column model ─────────────────────────
#
# Some corpora print their results as TABLES with a fixed column layout, and
# what is comparable in such a table is not "the line".  Within one row, some
# columns identify it (a GRID id, a MODE number, an element id), some are
# DERIVED from the quantities (a principal-stress ANGLE, a margin-of-safety
# ratio, a percentage fit error), and the rest are the physical quantities
# themselves.
#
# Two things make this a TABLE rather than a pattern list, and the difference
# is the whole point:
#
#   * a derived column can differ wildly while the quantities it derives from
#     agree — a ULP wobble in a near-zero denominator flips an angle by tens
#     of degrees — so it must be SKIPPED, not compared;
#   * but the row it sits on also carries real quantities, so "drop this
#     line" would stop comparing those too.  That is the gate quietly
#     relaxed, which is why the model names COLUMN POSITIONS.
#
# ``near_zero`` is the other half: a component of magnitude below it is a
# theoretical zero (a free-expansion thermal deck is full of them) whose
# relative error is meaningless, so it is compared absolutely.  Real
# magnitudes are still held to ``rtol``.

def _row_fields(rule: dict, tokens: list[str]) -> list[float] | None:
    """The physical fields of one row, per *rule*; ``None`` if the rule does
    not apply to this row."""
    m = rule.get('match') or {}
    if m.get('first_eq') is not None and (not tokens
                                          or tokens[0] != m['first_eq']):
        return None
    if m.get('first_eq_any') is not None and (not tokens
                                              or tokens[0] not in m['first_eq_any']):
        return None
    if m.get('first_int') and not (tokens
                                   and tokens[0].lstrip('+-').isdigit()):
        return None
    if m.get('second_eq') is not None and (len(tokens) < 2
                                           or tokens[1] != m['second_eq']):
        return None
    if m.get('second_int') and not (len(tokens) >= 2
                                    and tokens[1].lstrip('+-').isdigit()):
        return None
    if m.get('first_float') and not (tokens and _FLOAT_RE.match(tokens[0])):
        return None
    if m.get('n_tokens') is not None and len(tokens) != m['n_tokens']:
        return None
    if m.get('n_tokens_any') is not None and len(tokens) not in m['n_tokens_any']:
        return None
    if m.get('min_tokens') is not None and len(tokens) < m['min_tokens']:
        return None
    if m.get('max_tokens') is not None and len(tokens) > m['max_tokens']:
        return None
    fields = rule.get('fields') or {}
    if 'slice' in fields:
        lo, hi = fields['slice']
        if len(tokens) < hi:
            return None
        picked = tokens[lo:hi]
    else:
        indices = fields['indices']
        # A rule naming token 9 cannot apply to an 8-token row: the row is a
        # different shape, and reading past its end is an IndexError, not a
        # comparison.  The retired extractors each stated their own length;
        # this is that check, once.
        if len(tokens) <= max(indices):
            return None
        picked = [tokens[i] for i in indices]
    try:
        return [float(t) for t in picked]
    except ValueError:
        return None


def _fields_close(gen: list[float], base: list[float], *,
                  rtol: float, near_zero: float) -> bool:
    """Magnitude-aware compare of two aligned field lists."""
    if len(gen) != len(base):
        return False
    for g, b in zip(gen, base):
        if g == b:
            continue
        d = abs(g - b)
        if abs(b) >= near_zero:
            if d / max(abs(b), abs(g), 1e-30) > rtol:
                return False
        elif d > near_zero:
            return False
    return True


def field_lockstep(gen_text: str, base_text: str, *, model: dict,
                   drop=(), rtol: float = DEFAULT_RTOL,
                   max_diffs: int | None = 20) -> list[tuple]:
    """Lockstep, comparing the declared PHYSICAL columns of each table.

    Rows outside a declared table, and rows of a table whose shape matches no
    rule, fall back to ``numclose`` — so an unrecognised row is compared
    rather than skipped, and a corpus's unknown table cannot become a region
    nobody checks.
    """
    gl = normalize(gen_text, drop)
    bl = normalize(base_text, drop)
    near_zero = float(model.get('near_zero', 1e-3))
    titles: dict[str, dict] = {}
    for table in model.get('tables', ()):
        for title in table.get('titles', ()):
            titles[title] = table
    elem_re = re.compile(model['elemtype_line']) \
        if model.get('elemtype_line') else None

    diffs: list[tuple] = []
    gen_ctx = ('', '')
    base_ctx = ('', '')
    for i in range(max(len(gl), len(bl))):
        # The context advances on EVERY line, not only on a line that
        # differs: a table's title and its element-type lines are usually
        # identical on both sides, and tracking them only while looking at a
        # difference leaves every row of such a table with no context —
        # which silently sends the whole table down the fallback.
        if i < len(gl):
            gen_ctx = _advance(gen_ctx, gl[i], titles, elem_re)
        if i < len(bl):
            base_ctx = _advance(base_ctx, bl[i], titles, elem_re)
        if i >= len(gl):
            diffs.append((i, '(missing)', bl[i]))
        elif i >= len(bl):
            diffs.append((i, gl[i], '(missing)'))
        elif gl[i] != bl[i]:
            gf = _fields_for(titles, gen_ctx, gl[i])
            bf = _fields_for(titles, base_ctx, bl[i])
            if gf is not None and bf is not None:
                if not _fields_close(gf, bf, rtol=rtol, near_zero=near_zero):
                    diffs.append((i, gl[i], bl[i]))
            elif not numclose(gl[i], bl[i], rtol=rtol, atol=near_zero):
                diffs.append((i, gl[i], bl[i]))
        if max_diffs is not None and len(diffs) >= max_diffs:
            more = max(len(gl), len(bl)) - i - 1
            if more > 0:
                diffs.append((None, '...', f'({more} more lines)'))
            break
    return diffs


def _advance(ctx: tuple, line: str, titles: dict, elem_re) -> tuple:
    """The table/element-type context after *line*."""
    if elem_re:
        m = elem_re.search(line)
        if m:
            return (ctx[0], m.group(1).replace(' ', ''))
    flat = line.replace(' ', '')
    return (flat, '') if flat in titles else ctx


def _fields_for(titles: dict, ctx: tuple, line: str) -> list[float] | None:
    """Apply the context's table rules to *line*, in the order declared."""
    table = titles.get(ctx[0])
    if table is None:
        return None
    # An element type the model does not name takes the DEFAULT branch (the
    # '' key), which is how such a model is written: `BAR` has its own row
    # shapes and every other element falls through to the shared ones.  A
    # lookup that found nothing here would send the whole table down the
    # fallback, which is how a declared model silently stops applying.
    if table.get('by_elemtype'):
        by_type = table['by_elemtype']
        rules = by_type.get(ctx[1], by_type.get(''))
    else:
        rules = table.get('rows')
    tokens = line.split()
    for rule in rules or ():
        got = _row_fields(rule, tokens)
        if got is not None:
            return got
    return None
