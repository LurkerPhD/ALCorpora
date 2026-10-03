"""Run a corpus's acceptance plan, from the command line.

The three per-corpus benchmark programs this replaces each had their own CLI
(``-x`` here, ``-b`` there, ``-d`` again) and their own summary.  There is one
mechanism now, so there is one command, and it takes the thing that describes
the corpus: its plan.

    # the whole plan
    python3 -m protocolize.edd.bench_cli build_mystran

    # one case, and whatever it restarts from
    python3 -m protocolize.edd.bench_cli build_mystran --case bar_01
    python3 -m protocolize.edd.bench_cli build_mystran --case D01-01-1C

    # what the plan would discover and run, without running it
    python3 -m protocolize.edd.bench_cli build_mystran --list

Exit status is the plan's verdict: 0 when the plan's own summary is met, 1
otherwise.  That is the contract the callers of the old scripts relied on —
``isolate_stem_check.py`` and ``build_nastran_manual.sh`` both branch on it —
so it is what this preserves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bench, bench_suite

#: Where a corpus's plan lives, under its build directory.
PLAN_NAME = 'verification_plan.json'


def _target(plan, build_dir: Path, exe: Path | None) -> Path | None:
    """Where the product under test is, when the plan names one.

    A product corpus's binary is not always at the same place: the harness
    passes it explicitly, a mixed build puts it under ``curr/bin``, and a
    reference tree under the tree's own ``bin``.  The search order is the one
    that makes ``-b <tree>`` mean what a caller expects — the build being
    TESTED — and ``--exe`` overrides it, as the old ``-x`` did.
    """
    if exe is not None:
        return Path(exe)
    if plan.target.kind != 'product':
        # A tree corpus is judged where the build system put it: the edd flow
        # passes `<tree>/curr` as the build directory, so `curr` is where the
        # CTest registrations live.  Taking the build root here found no
        # registrations at all and reported the suite clean with zero checks
        # behind it — which is the failure the plan's own floors exist to
        # catch, caught by them.
        #
        # `<tree>/build` is the SECOND layout, and a real one: CMake's default
        # out-of-source build writes its registrations under `build/`, so a
        # corpus measured in its own single tree (the pure-Fortran reference
        # onboarding is verified against) keeps them there.  Without it the
        # search found no registrations and reported TOTAL 0 — a suite that
        # judged nothing, which is exactly what onboarding refuses.
        for candidate in (build_dir / 'curr' / 'build',
                          build_dir / 'curr',
                          build_dir / 'build',
                          build_dir):
            if (candidate / 'CTestTestfile.cmake').is_file():
                return candidate
        return build_dir / 'curr' / 'build'
    name = plan.target.product
    for candidate in (build_dir / 'curr' / 'bin' / name,
                      build_dir / 'bin' / name):
        if candidate.is_file():
            return candidate
    return build_dir / 'curr' / 'bin' / name


def _plan_and_env(build_dir: Path, origin: Path | None,
                  exe: Path | None = None):
    from protocolize.verification_plan import VerificationPlan
    plan = VerificationPlan.load(build_dir / PLAN_NAME)
    cfg = {'target_dir': str(build_dir), 'tree_root': str(build_dir),
           'protocol': str(build_dir / 'protocol_fortran.json'),
           '_config_dir': build_dir}
    env = bench.plan_env(plan, build_dir, cfg,
                         origin_dir=origin or build_dir / 'origin_release',
                         exe_path=_target(plan, build_dir, exe))
    return plan, env


def _resolve_selectors(selectors: list[str], cases: list) -> list[str]:
    """Turn each ``--case`` selector into the case ids it names.

    A selector is a case id, or the DECK's own relative path — the spelling
    the flow that attributes a single symptom has, because it is looking at
    one deck's output (``vic/7/S30``).  A prefix matches, as the flag this
    replaces did.

    It is resolved HERE rather than in the runner so that the runner's
    contract stays "a set of case ids", and it is refused when it names
    nothing: a selector that matched no case would run an empty suite and
    report it clean, which is the one failure attribution cannot survive.
    """
    picked: list[str] = []
    for sel in selectors:
        exact = [c.id for c in cases if c.id == sel]
        if exact:
            hits = exact
        else:
            low = sel.lower().rstrip('/')
            hits = [c.id for c in cases
                    if str(c.deck_rel or '').lower().startswith(low)
                    or c.id.lower().startswith(low)]
        if not hits:
            raise SystemExit(
                f'ERROR: --case {sel!r} names no case this plan discovers.\n'
                f'       A case id looks like {cases[0].id!r}; a deck prefix '
                f'like a deck_rel, e.g. {next((str(c.deck_rel) for c in cases if c.deck_rel), "<<none>>")!r}')
        if len(hits) > 1:
            print(f'[bench] {sel!r} matched {len(hits)} cases: '
                  f'{", ".join(sorted(hits)[:6])}'
                  f'{" ..." if len(hits) > 6 else ""}', file=sys.stderr)
        picked.extend(hits)
    return picked


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description='Run a corpus\'s acceptance plan (verification_plan.json)')
    p.add_argument('build_dir',
                   help='the corpus build tree holding verification_plan.json')
    p.add_argument('--case', '-t', action='append', default=[],
                   metavar='ID',
                   help='run only this case (repeatable); its restart '
                        'prerequisites are included automatically')
    p.add_argument('--origin', '-b', type=Path, default=None,
                   help='the golden origin (default: <build>/origin_release)')
    p.add_argument('--exe', '-x', type=Path, default=None,
                   help='the binary under test (default: <build>/curr/bin/'
                        '<product>, then <build>/bin/<product>)')
    p.add_argument('--jobs', '-j', type=int, default=None,
                   help='parallelism for a suite run (default: all cores)')
    p.add_argument('--list', action='store_true',
                   help='print the cases the plan discovers, and stop')
    args = p.parse_args(argv)

    build_dir = Path(args.build_dir).resolve()
    if not (build_dir / PLAN_NAME).is_file() and \
            not (build_dir / (PLAN_NAME + '.gz')).is_file():
        print(f'ERROR: no {PLAN_NAME} under {build_dir}', file=sys.stderr)
        return 1

    plan, env = _plan_and_env(build_dir, args.origin, args.exe)
    # `-j` is a DELIBERATE override for an experiment.  Without it the width
    # stays whatever the plan declares, because a suite's width is part of its
    # measurement — re-deriving it from the core count here would discard the
    # declared value a second time, after `bench.plan_env` had already
    # honoured it.
    if args.jobs:
        env.jobs = args.jobs

    cases = bench_suite.discover(plan, env)
    if args.list:
        for case in cases:
            print(case.id)
        return 0

    only = _resolve_selectors(args.case, cases) if args.case else None
    result = bench_suite.run(plan, env, only=only)
    print(result.output, end='')
    if not result.ok and result.counts.crash:
        # A crash is not a difference: say so in the status too, so a caller
        # branching on the exit code does not read "it ran and differed".
        return 2
    return 0 if result.ok else 1


if __name__ == '__main__':
    sys.exit(main())
