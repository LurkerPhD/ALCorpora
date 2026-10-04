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

    Update MERGES.  A column the caller does not pass keeps whatever the row
    already said, because the row holds two different kinds of fact: the
    VERDICT columns, which a run produces, and the DESCRIPTIVE ones
    (`language`, `build_system`, `verification`, `notes`), which say what the
    project IS.  Measured while re-proving the minimal samples: a blanking
    update turned `Fortran,CMake+Ninja,ctest` into `fortran,,` — a re-prove
    that reached the SAME verdict destroyed what the row said about the
    project, and the CSV then read as if that had been measured.
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
    row = {c: rows.get(number, {}).get(c, '') for c in CSV_COLUMNS}
    row.update({c: v for c, v in values.items() if c in CSV_COLUMNS})
    row['number'] = f'{number:03d}'
    row['name'] = name
    rows[number] = row
    import csv
    BENCH.mkdir(parents=True, exist_ok=True)
    with BENCH_CSV.open('w', encoding='utf-8', newline='') as fh:
        # `lineterminator='\n'` because the csv module defaults to '\r\n', and
        # a machine-written file the repo diffs on every admission should not
        # show its WHOLE self as changed over a one-row edit.
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator='\n')
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


def _submodule_report(dest: Path) -> 'str | None':
    """git's own account of *dest*'s submodules, or None if it is not a checkout.

    `.git` is tested FIRST because git discovers repositories by walking UP:
    a copied plain directory that happens to sit inside some other repository
    would otherwise answer with ITS parent's submodules — and the update below
    would then run against the wrong tree.
    """
    if not (dest / '.git').exists():
        return None
    r = subprocess.run(['git', 'submodule', 'status', '--recursive'],
                       cwd=str(dest), capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _outstanding(report: str) -> list[str]:
    """The submodules git says are NOT in place.

    `git submodule status` marks them: ``-`` never initialized, ``+`` at the
    wrong commit, ``U`` in conflict.  A submodule that IS in place is prefixed
    with a space — so the filesystem must not be asked instead, because an
    uninitialized submodule leaves its directory behind, EMPTY (measured), and
    "the directory exists" is true for exactly the state this detects.
    """
    return [l for l in report.splitlines() if l[:1] in '-+U']


def _fetch_submodules(dest: Path, *, retries: int = 3) -> None:
    """Bring a checkout's submodules in — the LAST thing fetching does.

    A checkout that vendors libraries is not fetched until its submodules are,
    so this belongs here and nowhere else: anywhere later (the build step, a
    prepare op) the same command would arrive as something to diagnose rather
    than as part of the checkout, and a build failing on a missing directory
    names the symptom instead of the fetch that should have brought it.

    Nothing is asked of a tree that does not declare submodules: a non-checkout
    has no ``.git``, and a checkout with nothing vendored reports nothing, so
    both return before any command runs.  Where there IS something to bring in,
    the check is git's own report, and it is repeated after each attempt —
    a partial fetch over a flaky link leaves the rest outstanding, and the
    second attempt picks up what the first missed.  An update that does not
    settle it after *retries* attempts raises rather than handing the build a
    directory that was never filled.
    """
    report = _submodule_report(dest)
    if report is None or not report.strip():
        return
    for attempt in range(1, retries + 1):
        out = _outstanding(report)
        if not out:
            return
        print(f'  submodules: {len(out)} outstanding, fetching '
              f'(attempt {attempt}/{retries})', file=sys.stderr)
        subprocess.run(['git', 'submodule', 'update', '--init', '--recursive'],
                       cwd=str(dest), capture_output=True, text=True)
        report = _submodule_report(dest) or ''
    out = _outstanding(report)
    if out:
        raise FetchError(
            f'submodules still not in after {retries} attempts: '
            + ', '.join(l.split()[-1] for l in out))


def fetch(source: str, dest: Path, *, retries: int = 3) -> None:
    """Put the source at *dest*: clone a URL, copy a local path.

    A shallow clone over a flaky link fails intermittently (`GnuTLS recv
    error: The TLS connection was non-properly terminated`), so a URL is
    retried before giving up — and giving up RAISES with the reason rather
    than crashing with a traceback.

    **Submodules come with it** (see :func:`_fetch_submodules`): checked,
    fetched if outstanding, re-checked, and only then is the fetch done.
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
                break
            last = (r.stderr or r.stdout).strip()
            tail = last.splitlines()[-1] if last else '?'
            print(f'  clone attempt {attempt}/{retries} failed: {tail}',
                  file=sys.stderr)
        else:
            raise FetchError(f'could not clone {source}: {last}')
    else:
        try:
            shutil.copytree(source, dest, symlinks=True)
        except OSError as exc:
            # The local-path half has to keep the same promise the clone half
            # does.  Measured: a mistyped local path exited through a raw
            # `FileNotFoundError` traceback, so the entry point's contract —
            # exit 1 with the reason — was broken by the one branch that never
            # got the treatment.
            raise FetchError(f'could not copy {source}: {exc}') from exc
    _fetch_submodules(dest, retries=retries)


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

    Returns ``(ok, log, prepare_ops)`` — the ops the caller DECLARED
    (``--prepare``), which is what the protocol records. This script does not
    invent a FIX: a build that fails is diagnosed by the SKILL, which
    researches the remedy and hands it back as an idempotent op.

    Submodules are NOT handled here — they are part of fetching, so they are
    already in by the time this runs (see `fetch`).
    """
    log = ''
    declared: list = list(getattr(args, 'prepare_ops', []) or [])
    for op in declared:
        if _prepare_done(op, src):
            continue
        ok, out = run(op['run'], cwd=src)
        log += f'$ {op["run"]}\n{out[-1000:]}\n'
        if not ok:
            # A DECLARED op that failed is reported HERE, by name, and stops
            # the run. Swallowing it sends the failure to whatever the build
            # complains about next — a missing directory, a wrong flag — and
            # that names the symptom instead of the preparation that was
            # supposed to prevent it.
            return False, log, declared
    for line in (args.configure, args.build, args.test):
        if not line:
            continue
        cmd = line.replace('{src}', str(src))
        ok, out = run(cmd, cwd=src)
        log += f'$ {cmd}\n{out[-2000:]}\n'
        if not ok:
            return False, log, declared
    return True, log, declared


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
                declaration: Path | None = None,
                notes: str = 'fetched from source') -> int:
    """Steps 3-4: the pipeline. Returns 0 when LEGAL, and records the row.

    *notes* says what THIS admission is.  A re-prove passes nothing, so the
    row keeps the description it already had instead of every replay
    relabelling an in-tree sample as "fetched from source".
    """
    from protocolize.onboard.extract import (ExtractionError,
                                             capture_verification,
                                             extract_and_prove)
    from protocolize.verification_plan import VerificationPlan
    from protocolize.verify import cli

    def fail(why, **cols):
        print(f'NOT LEGAL — {why}', file=sys.stderr)
        # The VERDICT columns only, plus `language` when this run actually
        # learned it.  Anything not passed keeps whatever the row already
        # said — a run that failed before reading the sources must not blank
        # a column it never measured.
        record_row(number, src_name, native='yes', **cols, notes=why)
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
    row = dict(language=lang, native='yes', build_protocol_a='yes',
               verify_on_project_b='yes', verify_on_replay_b='yes',
               status='PASS')
    # Only a FIRST admission gets to label the row.  A re-prove reached the
    # same verdict about the same project, so it must not overwrite what the
    # row says the project IS — measured: replaying a tracked in-tree sample
    # relabelled it "fetched from source", which is false about it.
    if notes:
        row['notes'] = notes
    record_row(number, src_name, **row)
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

    The benchmark directory ITSELF can be the source.  The minimal samples are
    laid out that way (a `CMakeLists.txt` at the top with a `source/` beside
    it, tracked in place rather than checked out into a subdirectory), and
    without this branch the search picks the first SUBdirectory holding a
    build system — `source/`, which holds none — and re-proving the sample
    dies on "does not appear to contain CMakeLists.txt".  The project's own
    statement wins over the search only when there is nothing to search.
    """
    for marker in ('CMakeLists.txt', 'Makefile', 'configure'):
        if (dest / marker).is_file():
            return dest
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
    """Run the admission flow — delegated to the single entry point.

    The flow itself lives in `admit.py` at the repository root: that is the
    DRIVER, and this script is the mechanical half it drives. One
    implementation, deliberately — two copies of a flow drift apart, and the
    copy that drifts is always the one nobody runs.

    `globals()` is handed over rather than letting `admit` re-load this file
    from disk, so the driver sees the SAME objects this module sees. That is
    what keeps the mechanical half patchable: a caller that replaces `native`
    or `BENCH_CSV` here replaces what the driver will actually call.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location('admit_driver',
                                                  REPO / 'admit.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load admit.py from the repository root')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(argv, namespace=globals())


if __name__ == '__main__':
    raise SystemExit(main())
