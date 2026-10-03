"""Benchmark counts, parsed from a runner's own output.

The verification plan's verdict is computed from a runner's printed summary,
so the parse of that summary is part of the tool, not of any one project.
This is the slice of the harness's counting that the plan executor needs;
the harness's project-specific stem statistics stay with the harness.

`BenchCounts.parse` is an EXTERNAL contract: it reads the lines a benchmark
program prints (`CRASHED`, `TIMEOUT`/`SLOW`, `FAIL <name>`, and the summary
`PASS: N, FAIL: M, TOTAL: T`).  A project whose runner prints those lines
needs no adapter; one that prints something else is a caveat for its plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Colour is stripped BEFORE matching, which is strictly stronger than
#: reading the raw text: a colour-wrapped ``FAIL`` is read as a failure,
#: where "strip the escapes" as an afterthought would have hidden it.
_ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

#: The counts line, as benchmark programs actually print it: ``PASS: 132``,
#: ``PASS: 215, FAIL: 0, TOTAL: 215``, ``PASS:      132``.  Matched with a
#: pattern rather than ``int(split()[1])``, which measured 0 passing cases
#: for a run printing `PASS: 215,` (``int('215,')``) and for one using colour.
_PASS_LINE = re.compile(r'\bPASS:\s*(\d+)')
_TOTAL_LINE = re.compile(r'\bTOTAL:\s*(\d+)')


def strip_ansi(text: str) -> str:
    """The text with terminal colour removed."""
    return _ANSI.sub('', text)


def _int_of(pattern: re.Pattern, text: str) -> int | None:
    """The LAST match's value, or ``None``.

    Last, not first: a suite may print an intermediate summary and then the
    final one, and the final line is the verdict.
    """
    found = pattern.findall(text)
    return int(found[-1]) if found else None


@dataclass
class BenchCounts:
    p: int = 0
    diff: int = 0
    timeout: int = 0
    crash: int = 0
    total: int = 0

    @classmethod
    def parse(cls, output: str) -> 'BenchCounts':
        c = cls()
        text = strip_ansi(output or '')
        for line in text.splitlines():
            s = line.strip()
            if s.startswith('CRASHED'):
                c.crash += 1
            elif s.startswith('TIMEOUT') or s.startswith('SLOW'):
                c.timeout += 1
            elif s.startswith('FAIL '):
                c.diff += 1

        # The summary is authoritative for PASS and TOTAL: a per-case count
        # cannot see the cases that never reported, which is the difference
        # between "nothing failed" and "nothing ran".  Its FAIL count is
        # deliberately NOT folded in — it is a different quantity (some
        # runners print FAIL = diff+timeout+crash), so merging it would
        # count every timeout twice.  The per-case lines own the kinds.
        summary = _int_of(_PASS_LINE, text)
        if summary is not None:
            c.p = summary
            c.total = _int_of(_TOTAL_LINE, text) or 0
        return c

    def is_clean(self) -> bool:
        """Nothing failed — AND something was actually seen.

        The second half is not decoration.  Measured: two of three corpora's
        summaries parsed to ``p=0``, so with no failures either this returned
        True for a run whose results nobody could read, and the origin built
        from it then judged every later batch.  Zero seen is not zero failed.
        """
        return (self.crash == 0 and self.timeout == 0 and self.diff == 0
                and self.p > 0)

    def tag_count(self, status: str) -> int:
        tag = status.lower()
        if tag == 'pass':
            return self.p
        if tag in ('build_fail', 'crashed'):
            return self.crash
        if tag == 'diff':
            return self.diff
        if tag in ('timeout', 'slow'):
            return self.timeout
        return 0

    def format(self, status: str) -> str:
        """``PASS(n)`` style label for one status; bare name when zero."""
        cnt = self.tag_count(status)
        return f"{status}({cnt})" if cnt else status
