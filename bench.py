#!/usr/bin/env python3
"""The fixed tools: one entry point, one atomic operation per subcommand.

Every operation this project performs on a corpus is here, spelled once, so
that a caller — a person, a script, or an agent — RUNS the operation instead
of writing a new one. That is the point: an agent that improvises a way to
"find the tree" or "clear the build" produces a different answer on each run,
and a benchmark whose own procedure is not reproducible cannot be used to
judge reproducibility. So the operations are fixed, and CLAUDE.md makes using
them mandatory.

Nothing here is a new mechanism. Each subcommand DELEGATES to the module that
already owns the behaviour — `admit.py` drives the flow, `verify.cli` runs a
plan, `protocolize.volatiles` measures what two runs disagree on. This file
only gives them one name and one place to be found.

    bench.py tree    DIR        which directory is the plan's ${TREE}
    bench.py wipe    DIR...     remove this tool's generated artifacts
    bench.py plan    DIR        the plan's problems, and the modes it declares
    bench.py verify  DIR        §B: run the plan, print the verdict
    bench.py evidence DIR [--shift-date]
                                run the reference TWICE and report the line
                                shapes that moved and are NOT yet declared
    bench.py row     DIR        this project's line in benchmark.csv
    bench.py admit   DIR [--…]  the full flow (delegates to admit.py)

Exit status: 0 on success, 1 on a failed verdict, 2 on a usage error. A
subcommand that cannot run at all says so on stderr and returns 1 — never a
traceback, because a caller branching on the exit code must not have to parse
a stack trace to learn that a path was wrong.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BENCH = REPO / 'benchmark'

#: This tool's own output beside a project.  Cleared by `wipe`, and NOT the
#: project's files: its sources, its build files, and the declarations it
#: ships about itself (`facts.json`, `declaration.json`, `admit.json`) are the
#: material a re-derivation CONSUMES and are deliberately left alone.
GENERATED_FILES = ('protocol.json', 'verification_plan.json')
GENERATED_DIRS = ('origin_release', 'benchmark_results', '.work')

#: Directories a benchmark folder holds that are OUTPUT, not its checkout.
#: Spelled here so "which directory is the tree" cannot drift from "which
#: directory is output".
_OUTPUT_DIRS = frozenset(GENERATED_DIRS) | {'results', 'build'}
_MARKERS = ('CMakeLists.txt', 'Makefile', 'configure')


# ── atomic operations ─────────────────────────────────────────

def tree_of(dest: Path) -> Path:
    """The directory a plan's `${TREE}` denotes.

    DELEGATES to the flow's own `_inner_source`, which is the one place that
    decides it. Re-deriving the rule here was measured to get it WRONG: a
    checkout whose build files sit a level down and whose top level carries no
    build-system marker (a hand-written make project with its Makefiles under
    `source/`) was reported as its own folder rather than the checkout, and
    the run then found no cases. One question, one implementation.
    """
    import admit
    return Path(admit.fetch_namespace()['_inner_source'](Path(dest).resolve()))


def cmd_tree(args: list[str]) -> int:
    if len(args) != 1:
        print('usage: bench.py tree DIR', file=sys.stderr)
        return 2
    print(tree_of(Path(args[0])))
    return 0


def cmd_wipe(args: list[str]) -> int:
    """Remove every artifact THIS TOOL generated, for one or more projects.

    The BUILD is not touched here. Clearing a build is `admit.py --readmit`'s
    job and it is the only place that knows how: an out-of-source build
    directory is removed whole, while a build living among the sources — where
    deleting the directory would delete the project — has only the state the
    build system itself generated removed. Measured: a reimplementation here
    deleted a hand-written make project's source directory, because that
    project's `tree_subdir` names where its Makefiles ARE, not where a build
    put its output.
    """
    if not args:
        print('usage: bench.py wipe DIR...', file=sys.stderr)
        return 2
    for a in args:
        d = Path(a).resolve()
        if not d.is_dir():
            print(f'{d} is not a directory', file=sys.stderr)
            return 1
        for base in {d, tree_of(d)}:
            for name in GENERATED_FILES:
                p = base / name
                if p.is_file():
                    print(f'  rm {_short(p)}')
                    p.unlink()
            for name in GENERATED_DIRS:
                for p in sorted(base.rglob(name)):
                    if p.is_dir() and '.git' not in p.parts:
                        print(f'  rm -r {_short(p)}')
                        _rmtree(p)
    return 0


def _short(p: Path) -> str:
    """*p* relative to the repo when it is under it, else absolute.

    A caller can point this at any directory, and a path outside the repo has
    no relative form — `relative_to` would raise where a printed name was all
    that was wanted.
    """
    try:
        return str(Path(p).relative_to(REPO))
    except ValueError:
        return str(p)


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def cmd_plan(args: list[str]) -> int:
    """Validate a plan and say which comparison modes it declares.

    The declared-mode counts are what make this useful to a caller writing
    rules: `0 mask` says the plan has no in-line trimming, which is exactly
    the situation that lets a clock reading and a judged value share a line
    and be dropped together.
    """
    if len(args) != 1:
        print('usage: bench.py plan DIR', file=sys.stderr)
        return 2
    from protocolize.verification_plan import VerificationPlan
    from protocolize.volatiles import declared_modes

    d = Path(args[0]).resolve()
    p = d / 'verification_plan.json'
    if not p.is_file():
        print(f'no verification_plan.json under {d}', file=sys.stderr)
        return 1
    plan = VerificationPlan.load(p)
    problems = plan.problems()
    drop, keep, mask = declared_modes(plan)
    print(f'{d.name}: '
          f'{len(drop)} drop / {len(keep)} keep / {len(mask)} mask pattern(s)')
    for text in drop:
        print(f'  drop  {text!r}')
    for text in keep:
        print(f'  keep  {text!r}')
    for rx, repl in mask:
        print(f'  mask  {rx!r} -> {repl!r}')
    if problems:
        print(f'NOT EXECUTABLE — {len(problems)} problem(s):', file=sys.stderr)
        for x in problems:
            print(f'  {x}', file=sys.stderr)
        return 1
    print('  plan: executable')
    return 0


def cmd_verify(args: list[str]) -> int:
    """Run §B against a project's plan, on whatever is on disk now.

    A verdict, printed and returned as the exit code. This does NOT clear
    anything: `bench.py wipe` and `admit.py --readmit` do that, and a verify
    that silently wiped would make "it passed" mean "it passed on whatever a
    previous run left".
    """
    if not args:
        print('usage: bench.py verify DIR', file=sys.stderr)
        return 2
    d = Path(args[0]).resolve()
    if not (d / 'verification_plan.json').is_file():
        print(f'no verification_plan.json under {d} — nothing to verify',
              file=sys.stderr)
        return 1
    return subprocess.run(
        [sys.executable, '-m', 'protocolize.verify.cli', str(d),
         '--tree', str(tree_of(d)), '--origin', str(d / 'origin_release')],
        cwd=str(REPO)).returncode


def cmd_evidence(args: list[str]) -> int:
    """What two reference runs DISAGREE on, minus what the plan already covers.

    The measurement a comparison rule is written against. Delegates to
    `protocolize.volatiles`, which owns the definition.
    """
    from protocolize import volatiles
    if not args:
        print('usage: bench.py evidence DIR [--shift-date]', file=sys.stderr)
        return 2
    d = Path(args[0]).resolve()
    rest = [str(d), '--tree', str(tree_of(d))] + list(args[1:])
    return volatiles.main(rest)


def cmd_row(args: list[str]) -> int:
    """This project's line in benchmark/benchmark.csv, or a plain refusal."""
    if len(args) != 1:
        print('usage: bench.py row DIR', file=sys.stderr)
        return 2
    import csv
    d = Path(args[0]).resolve()
    if not d.name[:3].isdigit():
        print(f'{d.name} carries no admission number', file=sys.stderr)
        return 1
    n = int(d.name[:3])
    csv_path = BENCH / 'benchmark.csv'
    if not csv_path.is_file():
        print('no benchmark/benchmark.csv', file=sys.stderr)
        return 1
    with csv_path.open(encoding='utf-8', newline='') as fh:
        for row in csv.DictReader(fh):
            if row.get('number') == f'{n:03d}':
                for k, v in row.items():
                    print(f'{k:>20}: {v}')
                return 0
    print(f'no row for {n:03d}', file=sys.stderr)
    return 1


def cmd_admit(args: list[str]) -> int:
    """The full flow for one project. Delegates to `admit.py`.

    Arguments after DIR are passed straight through, so this is the same
    entry point the regression uses and not a second implementation of it.
    """
    if not args:
        print('usage: bench.py admit DIR [--configure … --build … …]',
              file=sys.stderr)
        return 2
    import admit
    return admit.main(['--readmit'] + list(args))


# ── dispatch ──────────────────────────────────────────────────

_COMMANDS = {
    'tree': cmd_tree,
    'wipe': cmd_wipe,
    'plan': cmd_plan,
    'verify': cmd_verify,
    'evidence': cmd_evidence,
    'row': cmd_row,
    'admit': cmd_admit,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ('-h', '--help', 'help'):
        print(__doc__.split('\n\n')[0])
        print('\ncommands: ' + ', '.join(sorted(_COMMANDS)))
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    fn = _COMMANDS.get(cmd)
    if fn is None:
        print(f'unknown command {cmd!r}; known: '
              + ', '.join(sorted(_COMMANDS)), file=sys.stderr)
        return 2
    return fn(rest)


if __name__ == '__main__':
    raise SystemExit(main())
