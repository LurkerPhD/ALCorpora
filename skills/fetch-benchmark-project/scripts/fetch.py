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
BENCH_CSV = BENCH / 'benchmark.csv'
CSV_COLUMNS = ('number', 'name', 'language', 'build_system', 'native',
               'build_protocol_a', 'verify_on_project_b', 'verify_on_replay_b',
               'status', 'notes')


def record_row(number: int, name: str, **values) -> None:
    """Add or replace this project's row in `benchmark/benchmark.csv`.

    A CSV keyed by NUMBER rather than an append log: re-admitting a project
    updates its row in place instead of adding a second one, and the file
    stays sorted so it reads as the benchmark's current state.
    """
    rows: dict[int, dict] = {}
    if BENCH_CSV.is_file():
        import csv
        with BENCH_CSV.open(encoding='utf-8', newline='') as fh:
            for row in csv.DictReader(fh):
                try:
                    rows[int(row['number'])] = row
                except (KeyError, ValueError):
                    continue
    row = {c: values.get(c, '') for c in CSV_COLUMNS}
    row['number'] = f'{number:03d}'
    row['name'] = name
    rows[number] = row
    import csv
    BENCH.mkdir(parents=True, exist_ok=True)
    with BENCH_CSV.open('w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])


def next_number(bench: Path) -> int:
    """The SMALLEST number not already taken.

    Admission order, not density: the first free number is the next one, so a
    project that failed to admit leaves its number to be reused rather than
    leaving a hole that the next project skips over.
    """
    used: set[int] = set()
    for d in bench.glob('[0-9][0-9][0-9]-*'):
        try:
            used.add(int(d.name[:3]))
        except ValueError:
            continue
    n = 1
    while n in used:
        n += 1
    return n


class FetchError(RuntimeError):
    """The source could not be fetched."""


def fetch(source: str, dest: Path, *, retries: int = 3) -> None:
    """Put the source at *dest*: clone a URL, copy a local path.

    A shallow clone over a flaky link fails intermittently (`GnuTLS recv
    error: The TLS connection was non-properly terminated`), so a URL is
    retried before giving up — and giving up RAISES with the reason rather
    than crashing with a traceback.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.startswith(('http://', 'https://', 'git@')) or \
            source.endswith('.git'):
        last = ''
        for attempt in range(1, retries + 1):
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            r = subprocess.run(
                ['git', 'clone', '--depth', '1', source, str(dest)],
                capture_output=True, text=True)
            if r.returncode == 0:
                return
            last = (r.stderr or r.stdout).strip()
            tail = last.splitlines()[-1] if last else '?'
            print(f'  clone attempt {attempt}/{retries} failed: {tail}',
                  file=sys.stderr)
        raise FetchError(f'could not clone {source}: {last}')
    shutil.copytree(source, dest, symlinks=True)


def run(shell_line: str, cwd: Path) -> tuple[bool, str]:
    """Run a native command. A SHELL line, not an argv: a project's build is
    sometimes several commands (`make -C a && make -C b`), and describing it
    should not need a scripting layer."""
    r = subprocess.run(shell_line, shell=True, cwd=str(cwd),
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout or '') + (r.stderr or '')


def native(src: Path, args) -> tuple[bool, str]:
    """Build and test the project the way IT builds — step 1 and 2."""
    log = ''
    for line in (args.configure, args.build, args.test):
        if not line:
            continue
        cmd = line.replace('{src}', str(src))
        ok, out = run(cmd, cwd=src)
        log += f'$ {cmd}\n{out[-2000:]}\n'
        if not ok:
            return False, log
    return True, log


def _language(proto) -> str:
    """The project's dominant source language, from the protocol itself."""
    counts: dict[str, int] = {}
    for e in proto.compilation:
        counts[e.language or '?'] = counts.get(e.language or '?', 0) + 1
    if not counts:
        return '?'
    return max(counts, key=counts.get)


def prove_legal(dest: Path, src: Path, build: Path, number: int,
                src_name: str) -> int:
    """Steps 3-4: the pipeline. Returns 0 when LEGAL, and records the row."""
    from protocolize.onboard.extract import (ExtractionError,
                                             capture_verification,
                                             extract_and_prove)
    from protocolize.verification_plan import VerificationPlan
    from protocolize.verify import cli

    def fail(notes, **cols):
        print(f'NOT LEGAL — {notes}', file=sys.stderr)
        record_row(number, src_name, language=cols.pop('language', ''),
                   native='yes', **cols, notes=notes)
        return 1

    work = dest / '.work'
    try:
        proto, report = extract_and_prove(build, tree_root=src, work_root=work)
    except ExtractionError as exc:
        print(f'NOT LEGAL — §A failed (the protocol does not replay the '
              f'project):\n{exc}', file=sys.stderr)
        return fail('protocol does not replay the project',
                    build_protocol_a='no', verify_on_project_b='no',
                    verify_on_replay_b='no')
    print(f'  §A: {report.compared} products compared, OK')

    proto.save(dest / 'protocol.json')
    data = capture_verification(proto, build, src, project_root=src)
    data['protocol'] = 'protocol.json'
    plan = VerificationPlan.from_json(data)
    lang = _language(proto)
    if plan.problems():
        print('NOT LEGAL — §B: the captured plan is not executable:\n  '
              + '\n  '.join(plan.problems()), file=sys.stderr)
        record_row(number, src_name, language=lang, native='yes',
                   build_protocol_a='yes', verify_on_project_b='no',
                   verify_on_replay_b='no', status='FAIL_B_PLAN',
                   notes='captured plan is not executable')
        return 1
    plan.save(dest / 'verification_plan.json')

    origin = dest / 'origin_release'
    if plan.golden.source == 'reference_run':
        from protocolize.verify import reference, suite
        cfg = {'target_dir': str(dest), 'tree_root': str(src),
               'protocol': str(dest / 'protocol.json'), '_config_dir': dest}
        env = reference.plan_env(plan, dest, cfg, origin_dir=origin)
        env.tree = src.resolve()
        goldens = suite.ensure_reference(plan, env)
        if goldens == 0:
            return fail('reference run captured no goldens',
                        language=lang, build_protocol_a='yes',
                        verify_on_project_b='no', verify_on_replay_b='no',
                        status='FAIL_B_REF')
        print(f'  captured {goldens} goldens from the reference run')

    rc = cli.main([str(dest), '--tree', str(src), '--origin', str(origin)])
    if rc != 0:
        return fail(f"§B failed on the project's own build (exit {rc})",
                    language=lang, build_protocol_a='yes',
                    verify_on_project_b='no', verify_on_replay_b='no',
                    status='FAIL_B_REF')
    print("  §B: green on the project's own build")

    flat = work / 'tree'
    proto.save(flat / 'protocol.json')
    shutil.copy2(dest / 'verification_plan.json',
                 flat / 'verification_plan.json')
    rc = cli.main([str(flat), '--tree', str(flat), '--origin', str(origin)])
    if rc != 0:
        return fail(f'§B failed on the REPLAY (exit {rc})', language=lang,
                    build_protocol_a='yes', verify_on_project_b='yes',
                    verify_on_replay_b='no', status='FAIL_B_FLAT')
    print('  §B: green on the replay — the two verdicts agree')

    shutil.rmtree(work, ignore_errors=True)
    record_row(number, src_name, language=lang, native='yes',
               build_protocol_a='yes', verify_on_project_b='yes',
               verify_on_replay_b='yes', status='PASS',
               notes='fetched from source')
    print(f'LEGAL: benchmark/{dest.name}/ holds the source, protocol.json, '
          f'verification_plan.json')
    return 0


def _inner_source(dest: Path) -> Path:
    """The project's own checkout inside a benchmark directory."""
    skip = {'origin_release', '.work', 'results', 'build'}
    for d in sorted(dest.iterdir()):
        if d.is_dir() and d.name not in skip and not d.name.startswith('.'):
            return d
    raise FetchError(f'{dest} holds no source checkout')


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('source', help='github url, or a local path to copy')
    p.add_argument('--name', default=None,
                   help='the project name (default: the source basename)')
    p.add_argument('--configure', default='cmake -S {src} -B {src}/build -G Ninja',
                   help='the native CONFIGURE command (default: CMake+Ninja)')
    p.add_argument('--build', default='cmake --build {src}/build',
                   help='the native BUILD command')
    p.add_argument('--test', default='ctest --test-dir {src}/build',
                   help='the native VERIFY command')
    p.add_argument('--tree-subdir', default='build',
                   help='the build directory, relative to the source')
    p.add_argument('--readmit', action='store_true',
                   help='re-prove a project already in benchmark/ (its SOURCE '
                        'is the argument), after a renumber or a tool change')
    args = p.parse_args(argv)

    if args.readmit:
        dest = Path(args.source).resolve()
        src = _inner_source(dest)
        number = int(dest.name[:3])
        if not src.is_dir():
            print(f'REFUSED: {src} is not a directory', file=sys.stderr)
            return 1
        print(f're-proving #{number:03d} {dest.name}')
        # A renumber (or a tool change) invalidates the previous build: its
        # recorded paths name the OLD directory, so build afresh.
        shutil.rmtree(src / args.tree_subdir, ignore_errors=True)
        ok, log = native(src, args)
        if not ok:
            print('REFUSED: the project does not build and verify natively.\n'
                  + log, file=sys.stderr)
            record_row(number, dest.name.split('-', 1)[-1], native='no',
                       build_protocol_a='no', verify_on_project_b='no',
                       verify_on_replay_b='no', status='REFUSED',
                       notes='does not build/verify natively')
            return 1
        print('  native build + verify: OK')
        return prove_legal(dest, src, src / args.tree_subdir, number,
                           dest.name.split('-', 1)[-1])

    src_name = args.name or Path(args.source.rstrip('/')).stem
    src_name = src_name[:-4] if src_name.endswith('.git') else src_name
    n = next_number(BENCH)
    dest = BENCH / f'{n:03d}-{src_name}'
    print(f'admitting #{n:03d} {src_name} -> {dest}')

    if dest.exists():
        print(f'REFUSED: {dest} already exists', file=sys.stderr)
        return 1
    try:
        fetch(args.source, dest / src_name)
    except FetchError as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 1
    src = dest / src_name
    ok, log = native(src, args)
    if not ok:
        print('REFUSED: the project does not build and verify natively.\n' + log,
              file=sys.stderr)
        record_row(n, src_name, native='no', build_protocol_a='no',
                   verify_on_project_b='no', verify_on_replay_b='no',
                   status='REFUSED', notes='does not build/verify natively')
        return 1
    print('  native build + verify: OK')
    return prove_legal(dest, src, src / args.tree_subdir, n, src_name)


if __name__ == '__main__':
    raise SystemExit(main())
