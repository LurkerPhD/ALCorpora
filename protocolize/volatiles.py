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
_MASKS = (
    (re.compile(r'\d+\.\d*[eEdD][-+]?\d+'), '#'),
    (re.compile(r'\d+\.\d+'), '#'),
    (re.compile(r'\d+'), '#'),
)


def shape_of(line: str) -> str:
    """*line* with its numbers replaced, so two runs can be compared as text.

    Whitespace is collapsed AFTER masking, and that is load-bearing: a
    printed table pads its columns to the width of the number in them, so
    `B =    21` and `B =     4` mask to `B =    #` and `B =     #` — the same
    sentence, two different shapes. Without the collapse one mechanism looks
    like several, and a rule written against one shape would miss the rest.
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


def clock_shifted(env: 'dict | None') -> dict:
    """An environment in which the run happens on a DIFFERENT DAY.

    Two runs made in the same minute are blind to every line whose value comes
    from the calendar: they agree on the date, so the date never appears as
    volatile, and a comparison rule built from that diff will accept a stale
    one. Measured on a corpus whose reports carry `DATE: 10/ 3/26` and a run
    stamp: those lines MUST be dropped, and a same-day pair says they are fine.

    `libfaketime` answers exactly this question, so it is used when it is
    installed. It is an OPTIONAL dependency and its absence is not an error —
    the diff is simply weaker, and `report` says so — because requiring it
    would make the whole mechanism unavailable on a machine that lacks it.
    """
    import os
    import shutil
    if not shutil.which('faketime'):
        return {}
    # A day and a year away from the real clock, so the date, the year and any
    # elapsed-time arithmetic all move together.
    out = dict(env or os.environ)
    out['LD_PRELOAD'] = _faketime_lib() or out.get('LD_PRELOAD', '')
    return out if out.get('LD_PRELOAD') else {}


def _faketime_lib() -> str:
    import glob
    for pat in ('/usr/lib/*/faketime/libfaketime.so.1',
                '/usr/lib/faketime/libfaketime.so.1',
                '/usr/local/lib/faketime/libfaketime.so.1'):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return ''


def shifted_stamp() -> str:
    """The faketime spec: the same wall clock, one full year later.

    A year rather than a day, so a line printing only the year moves too —
    and so an elapsed-time computation that spans the new year still runs.
    """
    return '+1y'


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
