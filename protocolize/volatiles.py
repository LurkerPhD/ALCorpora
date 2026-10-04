"""Which lines a program prints DIFFERENTLY on two runs of the same binary.

This is the deterministic half of "how are two runs legitimately compared".
Run the program twice, diff the outputs, and the lines that moved are volatile
by MEASUREMENT rather than by anyone's judgement — no model, no inference, no
guess about what a timestamp looks like.

It is a LOWER BOUND, and everything downstream depends on saying so plainly: a
two-run diff finds only what HAPPENED to vary in those two runs. Measured on a
corpus whose own notes record that three related counters move together
(`B=21,C=20,R=23` vs `B=20,C=21,R=22`), a diff of two runs caught two of the
three, and a declaration built from it passed 132/132 that day and would have
failed on another. So this produces EVIDENCE — which is a far better starting
point than a guess — and never claims to be the answer.

What it reports is SHAPES, not lines. Two runs of the same deck print the same
sentence with different numbers in it, and the useful fact is the sentence:
`' END TIME: #:##:##'`, seen in 132 cases, is one mechanism; the literal
`' END TIME: 3:55:16'` is one observation. Shapes are what a comparison rule
is written against, so they are what this hands over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: A run's own numbers are what vary, so they are what gets masked. Floats are
#: masked before integers so `1.5e-3` does not become `#.#e-#`.
#:
#: Each mask also swallows the WHITESPACE RUN before the number, and that is
#: what makes a shape the SENTENCE rather than the sentence plus a column's
#: padding. A printed field pads to the width of the number in it, so two runs
#: of one program print `at 20:37:16.314` and `at 20:30: 3.493`: the padding
#: before the `3` is not part of what the line SAYS, but masking the number
#: alone left it in the shape (`at #:#:#` vs `at #:#: #`), so ONE volatile
#: mechanism became TWO shapes and neither matched the line the other saw.
#: Eating the run puts the padding on the number's side of the boundary, where
#: it belongs, and the two collapse to one shape.
_MASKS = (
    (re.compile(r'\s*\d+\.\d*[eEdD][-+]?\d+'), '#'),
    (re.compile(r'\s*\d+\.\d+'), '#'),
    (re.compile(r'\s*\d+'), '#'),
)


def shape_of(line: str) -> str:
    """*line* with its numbers replaced, so two runs can be compared as text.

    Whitespace is collapsed AFTER masking, and that is load-bearing: a
    printed table pads its columns to the width of the number in them, so
    `B =    21` and `B =     4` mask to `B =    #` and `B =     #` — the same
    sentence, two different shapes. Without the collapse one mechanism looks
    like several, and a rule written against one shape would miss the rest.
    The masks eating their own leading run is the same point at the boundary
    the collapse cannot reach (see `_MASKS`): a number's padding is not text.
    """
    out = line.rstrip('\n')
    for rx, repl in _MASKS:
        out = rx.sub(repl, out)
    return ' '.join(out.split())


@dataclass(frozen=True)
class Volatile:
    """One line SHAPE that moved between two runs."""
    shape: str
    cases: int                    # how many decks showed this shape move
    examples: tuple[str, ...]     # what it looked like, on each side

    def render(self) -> str:
        ex = ' | '.join(self.examples[:2])
        return f'  {self.cases:>4} cases: {self.shape!r}   e.g. {ex}'


#: A shape's `#` is where `shape_of` replaced a number.  In a LINE that spot
#: holds any numeric spelling `shape_of` recognizes — an integer, a decimal,
#: or an exponent form — so the rule has to accept all of them.
_SHAPE_NUMBER_RE = r'[\d.]+(?:[eEdD][-+]?\d+)?'

#: What replaces a `#` when the shape is turned back into a regex.  The number
#: takes the whitespace AROUND it, because the shape does not carry any: the
#: masks ate the run before it, so `END TIME:#` stands for ` END TIME: 18`
#: where the real line's space has nowhere in the shape to live.  Without this
#: the rule built from a measured shape matches none of the lines it was
#: measured on.
_SHAPE_NUMBER_SEP = r'\s*' + _SHAPE_NUMBER_RE + r'\s*'


def _literal_as_regex(piece: str) -> str:
    """*piece* as regex source: its text literal, its space runs flexible.

    The spaces MUST flex.  A shape collapses whitespace because a printed
    table pads its columns to the width of the number in them, so the real
    line's runs are wider than the shape's single space; a rule built from
    the shape without this matches nothing on the very decks it was measured
    on.
    """
    esc = re.escape(piece)
    # `re.escape` escapes a space on old Pythons and not on new ones; take
    # either form back to a bare space, then widen every run of them.
    esc = esc.replace('\\ ', ' ')
    return re.sub(r' +', r'\\s+', esc)


def drop_pattern(shape: str) -> str:
    """A `drop` regex matching a line of exactly this SHAPE.

    A measured shape (``shape_of`` output — numbers replaced by `#`,
    whitespace collapsed) becomes regex source by escaping its literal text
    and putting a number-matcher where each `#` stood.  It is anchored, so it
    matches the lines this shape names and not a longer line that merely
    begins the same way.
    """
    parts = shape.split('#')
    body = _SHAPE_NUMBER_SEP.join(_literal_as_regex(p) for p in parts)
    return f'^\\s*{body}\\s*$'


def _golden_files(root: Path) -> dict[Path, Path]:
    """Every captured golden under *root*, keyed the way its case names it.

    Keyed by the path RELATIVE to the golden root, because the two runs write
    the same tree: a case's output is at the same place on both sides, and the
    key is what pairs them up.
    """
    out: dict[Path, Path] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob('*')):
        if p.is_file():
            out[p.relative_to(root)] = p
    return out


def clock_shifted(env: 'dict | None' = None) -> dict:
    """An environment in which the run happens on a DIFFERENT DAY.

    Two runs made in the same minute are blind to every line whose value comes
    from the calendar: they agree on the date, so the date never appears as
    volatile, and a comparison rule built from that diff will accept a stale
    one. Measured on a corpus whose reports carry `DATE: 10/ 3/26` and a run
    stamp: those lines MUST be dropped, and a same-day pair says they are fine.
    Measured again, on that same corpus, that `TZ` moves exactly those lines:
    the same deck run at `Etc/GMT+12` printed `DATE: 10/ 3/26` and at
    `Pacific/Kiritimati` printed `DATE: 10/ 4/26`.

    The shift is `TZ`, and NOTHING ELSE. A timezone is a standard environment
    variable every machine has, and the two offsets are plain POSIX literals
    (`XXX+12` / `XXX-14`) that name no file — so this needs no package, no
    root, and no timezone database, which is what "self-contained" has to mean
    for a tool other people run. An earlier version used `libfaketime`, which
    is a package to install and a shared object to locate, and it is gone.

    The one limit, stated rather than hidden: a POSIX `TZ` offset is clamped
    by libc to 24 hours, so this moves a DAY and not a year. A line printing
    only a YEAR will therefore agree on both sides and stay invisible here —
    the same lower-bound caveat as everything else in this module, and the
    reason `report` tells the reader to check the project's own rules too.
    """
    import os
    out = dict(os.environ if env is None else env)
    out['TZ'] = SHIFTED_TZ
    return out


#: A POSIX TZ literal two extremes apart (~26 hours, so a run at each lands on
#: a different calendar day).  Written as the literal rather than a
#: `Continent/City` name on purpose: a NAME is resolved through the system
#: timezone database and fails where that database is absent, while the
#: literal is parsed by libc itself and works everywhere.
SHIFTED_TZ = 'XXX-14'
REAL_TZ = 'XXX+12'


def shifted_stamp() -> str:
    """A name for the shift, for a report to print."""
    return SHIFTED_TZ


def between(root_a: Path, root_b: Path, *, limit: int = 60) -> list[Volatile]:
    """Line shapes that differ between two reference runs' goldens.

    Returns them most-widespread first, because a shape seen in every case is
    one mechanism and a shape seen once is more likely to be noise.
    """
    a, b = _golden_files(Path(root_a)), _golden_files(Path(root_b))
    shared = sorted(set(a) & set(b))
    by_shape: dict[str, set[str]] = {}
    examples: dict[str, list[str]] = {}
    for rel in shared:
        try:
            la = a[rel].read_text(errors='replace').splitlines()
            lb = b[rel].read_text(errors='replace').splitlines()
        except OSError:
            continue
        # Line-by-line on the SHORTER side: a run that wrote more or fewer
        # lines moved a line COUNT, which is a difference worth seeing but not
        # one a line pattern can name.
        for x, y in zip(la, lb):
            if x == y:
                continue
            sh = shape_of(x)
            if shape_of(y) != sh:
                # The text itself differs, not just the numbers — a pattern
                # over numbers cannot describe it, so it is reported as its
                # own shape rather than silently folded into one.
                sh = f'{shape_of(x)}  !=  {shape_of(y)}'
            by_shape.setdefault(sh, set()).add(str(rel))
            if len(examples.setdefault(sh, [])) < 2:
                examples[sh].append(f'{x.strip()[:60]!r} vs {y.strip()[:60]!r}')
    ranked = sorted(by_shape.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [Volatile(shape=sh, cases=len(cases),
                     examples=tuple(examples[sh][:2]))
            for sh, cases in ranked[:limit]]


def report(root_a: Path, root_b: Path, *, same_day: bool = True) -> str:
    """A seam's worth of evidence, or an honest nothing.

    The empty case is stated rather than left blank: "two runs agreed" is a
    real finding, and it means the volatility has to come from somewhere else
    — the project's own comparison rules — rather than from this.

    *same_day* says whether the two runs happened on the same calendar day,
    which decides how much the evidence is worth. Two runs a minute apart
    agree on the date, so a date line is INVISIBLE to them, and the caller who
    knows that must be able to say so.
    """
    got = between(root_a, root_b)
    if not got:
        return ('Two runs of the same binary produced NO differing lines in '
                'any shared case. Either the program is deterministic in its '
                'report, or the two runs did not both produce output.')
    lines = [f'Two runs of the same binary differed on {len(got)} distinct '
             f'line shape(s). Each is a MEASUREMENT of what moved — a lower '
             f'bound, not the whole set, because a line that happened to '
             f'agree this time is invisible here:']
    lines += [v.render() for v in got]
    lines.append(
        'Treat a shape as evidence of a MECHANISM, not of one line: several '
        'shapes that name the same thing (a pool report, a statistics block, '
        'a timer) move together, and a rule that covers only the shape you '
        'were shown will un-cover the rest.')
    if same_day:
        lines.append(
            'CAVEAT, and it is not a small one: these two runs happened on the '
            'SAME CALENDAR DAY, so every line carrying a date still agreed and '
            'does NOT appear above. A report with a date, a year, or anything '
            'derived from the calendar will have kept them stable — read the '
            'project\'s own comparison rules for those, and drop them.')
    return '\n'.join(lines)


# ── declared coverage ─────────────────────────────────────────

def declared_modes(plan) -> tuple:
    """The `drop`/`keep`/`mask` a plan ALREADY declares, as raw pattern lists.

    A diff is only useful to its reader if it says which of the moving lines
    are ALREADY accounted for.  A shape the plan already drops is not news and
    must not be presented as if it were — the whole point of handing this to
    an agent is that the agent fills in the REMAINING rules, not the whole
    set again.
    """
    drop: list = []
    keep: list = []
    mask: list = []
    for a in (getattr(plan, 'assertions', None) or []):
        if not hasattr(a, 'get'):
            continue
        drop += list(a.get('drop') or [])
        keep += list(a.get('keep') or [])
        mask += list(a.get('mask') or [])
    return drop, keep, mask


def is_covered(a_line: str, b_line: str, modes: tuple, *,
               rtol: float | None = None,
               atol: float | None = None) -> bool:
    """Whether the plan's declared rules already make these two lines equal.

    Asked by RUNNING the rules over the pair rather than by matching patterns
    by eye: a `mask` that only moves part of a line, or a `drop` that happens
    to match, are both answers the rule itself gives, and re-deriving them
    here would be a second implementation that can disagree with the first.

    *rtol*/*atol* are the plan's declared numeric slack, and they must be
    passed whenever the plan compares with `numclose` — otherwise this
    answers "not covered" for a pair the VERDICT calls equal, which is how
    `evidence` reported 27 uncovered shapes on a corpus whose comparison had
    0 differences.

    WHAT THIS STILL CANNOT SEE: a difference in line COUNT, or a reordering.
    Two runs of one program may print the same lines a different number of
    times or in a different order; `lockstep` compares the normalized
    sequences and absorbs that, while this pairs line *i* with line *i*. A
    line-count shift therefore shows here as uncovered even though the
    verdict is green. That is the honest limit of a per-line question, and it
    is why "evidence empty" is not by itself the completion test — the
    verdict is.
    """
    from protocolize.verify import compare
    drop, keep, mask = modes
    if not (drop or keep or mask):
        return False
    na = compare.normalize(a_line, drop, keep, mask)
    nb = compare.normalize(b_line, drop, keep, mask)
    if na == nb:
        return True
    # `normalize` returns the surviving LINES; a single line in gives 0 or 1
    # back. Only a one-line-each pair can be judged numerically.
    if rtol is not None and atol is not None and len(na) == 1 and len(nb) == 1:
        return not compare.numclose(na[0], nb[0], rtol=rtol, atol=atol)
    return False


def declared_slack(plan) -> tuple:
    """The `rtol`/`atol` the plan compares with, from its lockstep assertions.

    Read here for the same reason `declared_modes` is: coverage has to be
    asked with the SAME comparison the verdict uses. A pair inside the
    declared tolerance is not "uncovered" — it is a pair the verdict calls
    equal, and reporting it would send the reader to write a rule for a
    difference that does not exist.
    """
    for a in (getattr(plan, 'assertions', None) or []):
        if hasattr(a, 'get') and a.get('kind') in ('text_lockstep',
                                                   'field_assertion'):
            from protocolize.verify import compare
            return (float(a.get('rtol', compare.DEFAULT_RTOL)),
                    float(a.get('atol', compare.DEFAULT_ATOL)))
    return None


def uncovered(root_a: Path, root_b: Path, modes: tuple,
              *, slack: tuple | None = None, limit: int = 40) -> list:
    """Line shapes that differ between two runs AND are not yet declared.

    The SAME comparison the verdict makes: apply the plan's `drop`/`keep`/`mask`
    to each file FIRST, then compare the surviving sequences position by
    position, with the plan's numeric slack. Whatever a rule does not account
    for is what comes back.

    Normalizing first is what makes this agree with `lockstep`, and it is not
    a detail. Asked per RAW line — "is this raw pair covered?" — this reported
    27 uncovered shapes on a corpus whose comparison had ZERO differences,
    because a rule that DROPS a varying line changes the line COUNT, and a
    per-position question then mispairs everything after it. Normalizing
    first removes that whole class: the dropped line is gone from both sides,
    the counts agree, and the shift never appears.
    """
    from protocolize.verify import compare
    drop, keep, mask = modes
    rtol, atol = slack if slack else (compare.DEFAULT_RTOL, compare.DEFAULT_ATOL)
    a, b = _golden_files(Path(root_a)), _golden_files(Path(root_b))
    by_shape: dict[str, set] = {}
    examples: dict[str, list] = {}

    def note(sh: str, rel: str, example: str) -> None:
        by_shape.setdefault(sh, set()).add(rel)
        ex = examples.setdefault(sh, [])
        if len(ex) < 2:
            ex.append(example)

    for rel in sorted(set(a) & set(b)):
        try:
            ta = a[rel].read_text(errors='replace')
            tb = b[rel].read_text(errors='replace')
        except OSError:
            continue
        na = compare.normalize(ta, drop, keep, mask)
        nb = compare.normalize(tb, drop, keep, mask)
        if len(na) != len(nb):
            # A difference the declared rules did NOT account for, since a
            # line both sides drop cannot change the count. One shape, not
            # one per mispaired line after it.
            note(f'<line count differs after the rules: {len(na)} vs {len(nb)}>',
                 str(rel), f'{len(na)} lines vs {len(nb)} lines')
            continue
        for x, y in zip(na, nb):
            if x == y:
                continue
            if compare.numclose(x, y, rtol=rtol, atol=atol):
                continue
            sh = shape_of(x)
            if shape_of(y) != sh:
                sh = f'{shape_of(x)}  !=  {shape_of(y)}'
            note(sh, str(rel), f'{x.strip()[:70]!r} vs {y.strip()[:70]!r}')

    ranked = sorted(by_shape.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [Volatile(shape=sh, cases=len(c), examples=tuple(examples[sh]))
            for sh, c in ranked[:limit]]


# ── the two runs ──────────────────────────────────────────────

def capture_twice(plan, env, work_root: Path, *, shift_date: bool = False):
    """Run the REFERENCE through *plan* twice; return the two golden roots.

    The second run happens under a shifted `TZ` when *shift_date* is asked
    for, which is what makes calendar-derived lines move.  The environment
    reaches the child because `suite._run_process` runs it as
    ``{**os.environ, **case.env}`` — so setting `TZ` here is enough, and no
    part of the runner needs to know this exists.

    Returns ``(root_a, root_b)``, the two runs' captured outputs.
    """
    import dataclasses
    import os
    import shutil
    from protocolize.verify import suite as suite_mod

    work_root = Path(work_root)
    roots = []
    for i, shift in enumerate((False, shift_date)):
        dest = work_root / f'ref_{i}'
        if dest.exists():
            shutil.rmtree(dest)
        sub = dataclasses.replace(env, origin=dest)
        saved = None
        if shift:
            changed = clock_shifted()
            saved = {k: os.environ.get(k) for k in changed}
            os.environ.update(changed)
        try:
            n = suite_mod.ensure_reference(plan, sub)
        finally:
            if saved is not None:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        if n == 0:
            raise RuntimeError(
                f'reference run {i} captured nothing — the plan did not run')
        roots.append(dest)
    return roots[0], roots[1]


def main(argv: list[str] | None = None) -> int:
    """CLI: measure which lines two runs disagree on, and which are declared.

    Emits EVIDENCE for whoever writes the comparison rules — a human or an
    agent. It does not decide the rules and does not run an agent itself: it
    reports what MOVED and what is ALREADY covered, and stops.
    """
    import argparse
    import sys
    import tempfile
    from protocolize.verification_plan import VerificationPlan
    from protocolize.verify import cli as cli_mod

    p = argparse.ArgumentParser(description=main.__doc__.splitlines()[0])
    p.add_argument('build_dir',
                   help='the build tree holding verification_plan.json')
    p.add_argument('--tree', type=Path, default=None,
                   help='the tree the plan runs in (default: the build dir)')
    p.add_argument('--origin', type=Path, default=None,
                   help='unused for the capture; kept for symmetry with the '
                        'plan runner')
    p.add_argument('--exe', '-x', type=Path, default=None)
    p.add_argument('--shift-date', action='store_true',
                   help='run the second capture under a shifted `TZ`, so '
                        'calendar-derived lines move too')
    p.add_argument('--keep', type=Path, default=None,
                   help='keep the two captured trees here (for inspection)')
    args = p.parse_args(argv)

    build_dir = Path(args.build_dir).resolve()
    plan = VerificationPlan.load(build_dir / 'verification_plan.json')
    from protocolize.verify.cli import _plan_and_env
    _, env = _plan_and_env(build_dir, args.origin, args.exe, None, args.tree)

    work = Path(args.keep) if args.keep else Path(
        tempfile.mkdtemp(prefix='volatiles-'))
    work.mkdir(parents=True, exist_ok=True)
    try:
        root_a, root_b = capture_twice(plan, env, work,
                                       shift_date=args.shift_date)
        modes = declared_modes(plan)
        left = uncovered(root_a, root_b, modes, slack=declared_slack(plan))
    finally:
        if not args.keep:
            import shutil
            shutil.rmtree(work, ignore_errors=True)

    declared = sum(1 for m in modes if m)
    print(f'two runs of {build_dir.name}: second '
          f'{"shifted" if args.shift_date else "same day"}')
    print(f'the plan declares {len(modes[0])} drop / {len(modes[1])} keep / '
          f'{len(modes[2])} mask pattern(s)')
    if not left:
        print('NO undeclared differences: every line that moved is already '
              'covered by the plan\'s own rules.')
        return 0
    print(f'{len(left)} line shape(s) moved and are NOT yet covered — these '
          f'are what a rule still has to account for:')
    for v in left:
        print(v.render())
    print('Treat each shape as evidence of a MECHANISM: several shapes naming '
          'one thing move together. A fixed-width field that can SATURATE '
          '(`I8` printing a value too wide for it, showing as `********`) '
          'hides a difference rather than showing one, so a shape that looks '
          'perfectly stable may still be moving underneath.')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
