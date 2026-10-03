#!/usr/bin/env python3
"""Admit one project into `benchmark/`: number it, prove it legal, record it.

The mechanical half of the `fetch-benchmark-project` skill. What is different
per project — how it builds, how it verifies — is passed IN, because finding
those commands is a research result, not a constant.

A project is LEGAL when, in this order:

  1. it builds natively with its own toolchain;
  2. it verifies natively (its own tests pass);
  3. §A — a build generated from the protocol alone replays the project;
  4. §B — the plan is green on the project's build AND on the replay.

A failure at 1-2 is refused (not a legal project yet). A failure at 3-4 is
REPORTED with the recorded protocols still written, because that failure is
the next thing to develop — the point of a benchmark.

Exit status: 0 legal, 1 not legal, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BENCH = REPO / 'benchmark'


def next_number(bench: Path) -> int:
    """The next admission number: one past the highest already present."""
    n = 0
    for d in bench.glob('[0-9][0-9][0-9]-*'):
        try:
            n = max(n, int(d.name[:3]))
        except ValueError:
            continue
    return n + 1


def fetch(source: str, dest: Path) -> None:
    """Put the source at *dest*: clone a URL, copy a local path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.startswith(('http://', 'https://', 'git@')) or \
            source.endswith('.git'):
        subprocess.run(['git', 'clone', '--depth', '1', source, str(dest)],
                       check=True)
    else:
        shutil.copytree(source, dest, symlinks=True)


def run(argv: list[str], cwd: Path) -> tuple[bool, str]:
    r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)
    return r.returncode == 0, (r.stdout or '') + (r.stderr or '')


def native(src: Path, args) -> tuple[bool, str]:
    """Build and test the project the way IT builds — step 1 and 2."""
    log = ''
    for argv in (args.configure, args.build, args.test):
        if not argv:
            continue
        ok, out = run([a.format(src=src) for a in argv], cwd=src)
        log += f'$ {" ".join(argv)}\n{out[-2000:]}\n'
        if not ok:
            return False, log
    return True, log


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('source', help='github url, or a local path to copy')
    p.add_argument('--name', default=None,
                   help='the project name (default: the source basename)')
    p.add_argument('--configure', default='cmake -S {src} -B {src}/build -G Ninja',
                   help='the native CONFIGURE command (default: CMake+Ninja)')
    p.add_argument('--build', default='cmake --build {src}/build -j',
                   help='the native BUILD command')
    p.add_argument('--test', default='ctest --test-dir {src}/build',
                   help='the native VERIFY command')
    p.add_argument('--tree-subdir', default='build',
                   help='the build directory, relative to the source')
    args = p.parse_args(argv)
    # Split the command strings into argv, once.
    for k in ('configure', 'build', 'test'):
        v = getattr(args, k)
        setattr(args, k, v.split() if v else [])

    src_name = args.name or Path(args.source.rstrip('/')).stem
    src_name = src_name[:-4] if src_name.endswith('.git') else src_name
    n = next_number(BENCH)
    dest = BENCH / f'{n:03d}-{src_name}'
    print(f'admitting #{n:03d} {src_name} -> {dest}')

    # Step 0-2: fetch, build, verify — natively.
    if dest.exists():
        print(f'REFUSED: {dest} already exists', file=sys.stderr)
        return 1
    fetch(args.source, dest / src_name)
    src = dest / src_name
    ok, log = native(src, args)
    if not ok:
        print('REFUSED: the project does not build and verify natively.\n' + log,
              file=sys.stderr)
        return 1
    print('  native build + verify: OK')

    # Step 3-4: the pipeline.
    from protocolize.onboard.extract import (ExtractionError, capture_verification,
                                             extract_and_prove)
    from protocolize.verification_plan import VerificationPlan

    build = src / args.tree_subdir
    work = dest / '.work'
    try:
        proto, report = extract_and_prove(build, tree_root=src, work_root=work)
    except ExtractionError as exc:
        print(f'NOT LEGAL — §A failed (the protocol does not replay the '
              f'project):\n{exc}', file=sys.stderr)
        return 1
    print(f'  §A: {report.compared} products compared, OK')

    proto.save(dest / 'protocol.json')
    data = capture_verification(proto, build, src, project_root=src)
    data['protocol'] = 'protocol.json'
    plan = VerificationPlan.from_json(data)
    if plan.problems():
        print('NOT LEGAL — §B: the captured plan is not executable:\n  '
              + '\n  '.join(plan.problems()), file=sys.stderr)
        return 1
    plan.save(dest / 'verification_plan.json')

    # §B on the project's own build.
    from protocolize.verify import cli
    rc = cli.main([str(dest), '--tree', str(src)])
    if rc != 0:
        print(f'NOT LEGAL — §B failed on the project\'s own build (exit {rc})',
              file=sys.stderr)
        return 1
    print('  §B: green on the project\'s own build')

    # §B on the replay.
    flat = work / 'tree'
    proto.save(flat / 'protocol.json')
    shutil.copy2(dest / 'verification_plan.json', flat / 'verification_plan.json')
    rc = cli.main([str(flat), '--tree', str(flat)])
    if rc != 0:
        print(f'NOT LEGAL — §B failed on the REPLAY (exit {rc})', file=sys.stderr)
        return 1
    print('  §B: green on the replay — the two verdicts agree')

    shutil.rmtree(work, ignore_errors=True)
    print(f'LEGAL: benchmark/{dest.name}/ holds the source, protocol.json, '
          f'verification_plan.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
