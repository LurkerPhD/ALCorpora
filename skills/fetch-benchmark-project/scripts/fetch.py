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

from protocolize.output_layout import RESULTS_DIR  # noqa: E402

BENCH = REPO / 'benchmark'
BENCH_CSV = BENCH / 'benchmark.csv'
CSV_COLUMNS = ('number', 'name', 'language', 'build_system', 'verification',
               'native', 'build_protocol_a', 'verify_on_project_b',
               'verify_on_replay_b', 'status', 'notes')


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


def _prepare_done(op: dict, src: Path) -> bool:
    """Whether a prepare op is already applied, by its own `check`.

    A `check` is either a shell line (exit 0 = done) or a `{path, exists}`
    predicate over the tree. An op with no check runs every time — which is
    why the skill is told to give every op one.
    """
    check = op.get('check')
    if not check:
        return False
    if isinstance(check, str):
        return run(check, cwd=src)[0]
    if check.get('shell'):
        return run(check['shell'], cwd=src)[0]
    p = src / str(check.get('path', ''))
    exists = p.exists()
    return exists if check.get('exists', True) else not exists


def native(src: Path, args) -> tuple[bool, str, list]:
    """Build and test the project the way IT builds — steps 1 and 2.

    Returns ``(ok, log, prepare_ops)``. The prepare ops are the ones the
    caller DECLARED (``--prepare``): this script does not invent a fix — a
    build that fails is diagnosed by the SKILL, which researches the remedy
    and hands it back as an idempotent op. That is where the "which fix"
    judgement belongs, because it differs per project; here it is only run
    and recorded.
    """
    log = ''
    remedies: list = list(getattr(args, 'prepare_ops', []) or [])
    # APPLY the declared ops first, so the build runs on the prepared tree.
    # A fresh checkout reproduces the build because each op carries a check
    # and a `run`: `run` only fires when `check` says it is not done yet.
    for op in remedies:
        if _prepare_done(op, src):
            continue
        ok, out = run(op['run'], cwd=src)
        log += f'$ {op["run"]}\n{out[-1000:]}\n'
    for line in (args.configure, args.build, args.test):
        if not line:
            continue
        cmd = line.replace('{src}', str(src))
        ok, out = run(cmd, cwd=src)
        log += f'$ {cmd}\n{out[-2000:]}\n'
        if not ok:
            return False, log, remedies
    return True, log, remedies


def _language(proto) -> str:
    """The project's dominant source language, from the protocol itself."""
    counts: dict[str, int] = {}
    for e in proto.compilation:
        counts[e.language or '?'] = counts.get(e.language or '?', 0) + 1
    if not counts:
        return '?'
    return max(counts, key=counts.get)


def prove_legal(dest: Path, src: Path, build: Path, number: int,
                src_name: str, remedies: 'list | None' = None,
                facts: Path | None = None,
                declaration: Path | None = None) -> int:
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

    # RECORD the remedies that made the build work as PREPARE ops: they
    # carry a check, so a fresh checkout reproduces the build and a
    # repeat pays nothing.
    if remedies:
        proto.prepare = list(remedies) + list(proto.prepare or ())
    proto.save(dest / 'protocol.json')
    # `facts` and `declaration` are the project's OWN declaration, passed
    # through rather than searched for — see `capture_verification`.  Without
    # them a corpus that needs preparation stages none, its program exits 0
    # having written nothing, and every case reports "no output" against a
    # project that in fact works.
    data = capture_verification(proto, build, src, project_root=src,
                                declaration=declaration, facts=facts)
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
        from protocolize.verify import cli as cli_mod
        from protocolize.verify import reference, suite
        cfg = {'target_dir': str(dest), 'tree_root': str(src),
               'protocol': str(dest / 'protocol.json'), '_config_dir': dest}
        # The reference run is a RUN like any other — of the project's own
        # build — so its roots are resolved the same way `cli` resolves them
        # for a plan run.  Left to `plan_env`'s default the target would be
        # resolved against the benchmark folder rather than the tree the
        # build actually lives in, and a corpus whose products are beside
        # its sources would capture its goldens from a binary that is not
        # there.
        env = reference.plan_env(plan, dest, cfg, origin_dir=origin,
                                 exe_path=cli_mod._target(plan, src, None))
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


#: Directories a benchmark folder holds that are OUTPUT, not the checkout.
#: The run's own results directory comes from the tool rather than being
#: spelled again here, so the harness that WRITES it and the search that must
#: skip it cannot drift apart.
_OUTPUT_DIRS = frozenset({'origin_release', '.work', 'results', 'build',
                          RESULTS_DIR})


def _inner_source(dest: Path) -> Path:
    """The project's own checkout inside a benchmark directory.

    Chosen by what it HOLDS — a build system — rather than by name, so an
    output directory a run left behind (`benchmark_results/`) is not mistaken
    for the source.
    """
    dirs = [d for d in sorted(dest.iterdir())
            if d.is_dir() and not d.name.startswith('.')
            and d.name not in _OUTPUT_DIRS]
    for d in dirs:
        if (d / 'CMakeLists.txt').is_file() or (d / 'Makefile').is_file() \
                or (d / 'configure').exists():
            return d
    if dirs:
        return dirs[0]
    raise FetchError(f'{dest} holds no source checkout')


def _declared(value: str | None, src: Path) -> Path | None:
    """A declaration path from the command line, as the caller meant it.

    A path argument on a command line means what it means everywhere else —
    relative to the CURRENT directory.  But a facts file is also expected to
    travel beside the project it describes, so when the CWD-relative spelling
    names nothing the project-relative one is tried.

    Resolution only; whether the file is THERE is checked where it is used, so
    this stays a pure naming function.
    """
    if not value:
        return None
    p = Path(value)
    if p.is_absolute():
        return p
    return p if p.exists() else (src / p)


def _require_present(path: Path | None, what: str) -> Path | None:
    """Refuse a declared file that is not there, by name.

    Without this the missing path surfaces as a traceback from inside the
    capture, which says where the read failed and nothing about the fact that
    the caller's argument named no file.
    """
    if path is not None and not path.is_file():
        raise FetchError(
            f'{what} names no file: {path} does not exist')
    return path


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
    p.add_argument('--prepare', default=None,
                   help='JSON list of one-time prepare ops the SKILL diagnosed '
                        '(each with a `check` and a `run`); recorded into the '
                        'protocol so a fresh checkout reproduces the build')
    p.add_argument('--readmit', action='store_true',
                   help='re-prove a project already in benchmark/ (its SOURCE '
                        'is the argument), after a renumber or a tool change. '
                        'The benchmark FOLDER or its inner checkout both work.')
    p.add_argument('--facts', default=None,
                   help='JSON file of facts the project declares that its '
                        'tree cannot show (a restart chain, the preparation '
                        'its programs need to run). Read by the capture — see '
                        'the "facts declaration" section of the skill. A '
                        'relative path is resolved against the project source.')
    p.add_argument('--declaration', default=None,
                   help='the file where the project states its comparison '
                        'rules (volatile lines, numeric slack). Passed IN, '
                        'never searched for. A relative path is resolved '
                        'against the project source.')
    args = p.parse_args(argv)
    args.prepare_ops = []
    if args.prepare:
        args.prepare_ops = json.loads(Path(args.prepare).read_text())

    if args.readmit:
        dest = Path(args.source).resolve()
        # `--readmit` names the BENCHMARK FOLDER, but the inner checkout is
        # the natural thing to paste, and naming it used to die with
        # `ValueError: invalid literal for int() with base 10: 'NAS'` — a
        # traceback that says nothing about what was wrong.  A directory with
        # no leading number IS an inner checkout, so it is accepted.
        if not dest.name[:3].isdigit() and dest.parent.name[:3].isdigit():
            dest = dest.parent
        if not dest.name[:3].isdigit():
            print(f'REFUSED: {dest} is not a benchmark folder — its name does '
                  f'not begin with an admission number (001-…).  Pass the '
                  f'folder, e.g. benchmark/009-<name>.', file=sys.stderr)
            return 2
        src = _inner_source(dest)
        number = int(dest.name[:3])
        if not src.is_dir():
            print(f'REFUSED: {src} is not a directory', file=sys.stderr)
            return 1
        print(f're-proving #{number:03d} {dest.name}')
        # A renumber (or a tool change) invalidates the previous build: its
        # recorded paths name the OLD directory, so configure afresh.
        #
        # An IN-SOURCE build cannot be cleared by removing its directory — that
        # directory IS the checkout — so the build system's own GENERATED files
        # are removed instead and the sources are left alone.  Both halves
        # matter:
        #   * Removing the whole tree deletes the project (it is the source).
        #   * Removing NOTHING is not neutral either: a stale cache re-runs the
        #     project's configure against its own old answers, and a CMake
        #     `set(... CACHE ... FORCE)` in the project can then OVERRIDE the
        #     flags this run passes.  Measured: a corpus mapped a deprecated
        #     option to a new one by force, so `-DNEW=EMBEDDED` kept resolving
        #     to the OLD value, the build linked a system library that is not
        #     installed, and it failed as "does not build" — a diagnosis about
        #     the project for a fact about a leftover file.
        bd = src / args.tree_subdir
        generated = ((bd / 'CMakeCache.txt').is_file()
                     or (bd / 'build.ninja').is_file()
                     or (bd / 'CMakeFiles').is_dir())
        if not generated:
            pass
        elif bd != src:
            shutil.rmtree(bd, ignore_errors=True)
        else:
            # The build system's own state, and nothing else.  A hand-written
            # `Makefile` is deliberately absent from this list: only files
            # CMake is known to generate are touched, so a project's own file
            # can never be the thing this deletes.
            for name in ('CMakeCache.txt', 'CMakeFiles', 'build.ninja',
                         '.ninja_deps', '.ninja_log', 'cmake_install.cmake'):
                target = bd / name
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists():
                    target.unlink()
        ok, log, remedies = native(src, args)
        if not ok:
            # NOT recorded.  A re-prove's failure is "could not re-prove", not
            # "refused": the row already holds a state that was ESTABLISHED,
            # and overwriting it destroys that fact.  Measured: an operator's
            # malformed invocation (the CMake defaults against a make project)
            # turned an established PASS into REFUSED, and nothing in the CSV
            # said the run had been misconfigured rather than the project
            # having regressed.
            print('NOT RE-PROVED: the project does not build and verify with '
                  'the native commands given.\n' + log, file=sys.stderr)
            print(f'  the row for #{number:03d} is UNCHANGED — fix the '
                  f'--configure/--build/--test arguments and re-run.',
                  file=sys.stderr)
            return 1
        print('  native build + verify: OK')
        return prove_legal(dest, src, src / args.tree_subdir, number,
                           dest.name.split('-', 1)[-1], remedies,
                           facts=_require_present(
                               _declared(args.facts, src), '--facts'),
                           declaration=_require_present(
                               _declared(args.declaration, src),
                               '--declaration'))

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
    ok, log, remedies = native(src, args)
    if not ok:
        print('REFUSED: the project does not build and verify natively.\n' + log,
              file=sys.stderr)
        record_row(n, src_name, native='no', build_protocol_a='no',
                   verify_on_project_b='no', verify_on_replay_b='no',
                   status='REFUSED', notes='does not build/verify natively')
        return 1
    print('  native build + verify: OK')
    return prove_legal(dest, src, src / args.tree_subdir, n, src_name,
                       remedies,
                       facts=_require_present(
                           _declared(args.facts, src), '--facts'),
                       declaration=_require_present(
                           _declared(args.declaration, src), '--declaration'))


if __name__ == '__main__':
    raise SystemExit(main())
