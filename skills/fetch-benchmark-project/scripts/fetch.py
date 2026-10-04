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


def _clean_stale_products(src: Path, tree_subdir: str, log: str) -> str:
    """Remove what a previous build produced, so a changed flag takes effect.

    A build system that tracks its own command needs nothing here: ninja
    stores the command and re-runs it when it changes, so a CMake+ninja tree
    rebuilds by itself.  A HAND-WRITTEN Makefile does not — measured: a flag
    added to a Makefile left every object newer than its source, so the next
    `make` re-linked them and answered `Nothing to be done`, and a seam that
    had just fixed the flags was told its fix had failed when the fix had
    never reached a single file.

    So each Makefile in the tree is asked to clean itself — the project's OWN
    definition of "built" is the only rule that can know what one is.  It is
    best-effort, and it runs AFTER the declared ops, because for a project
    whose `clean` is itself broken the patch that fixes it is one of those
    ops (a Windows-authored Makefile spells both the build and the clean with
    the same broken tool — measured: `DELETE = del` beside a commented-out
    `#LNX DELETE = rm`).

    The step FAILING is not an error: a Makefile with no `clean` target has
    said what it has to say, and the build that follows reports the truth.
    """
    bd = src / tree_subdir
    if ((bd / 'CMakeCache.txt').is_file()
            or (bd / 'build.ninja').is_file()
            or (bd / 'CMakeFiles').is_dir()):
        return log
    for mk in sorted(bd.rglob('Makefile')) + sorted(bd.rglob('makefile')):
        _, out = run(f'make -C {mk.parent} clean', cwd=bd)
        log += f'$ make -C {mk.parent} clean\n{out[-500:]}\n'
    return log


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
    log = _clean_stale_products(src, args.tree_subdir, log)
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


def _run_plan(cli, argv) -> tuple[int, str]:
    """Run the plan, and KEEP what it printed.

    `cli.main` prints the verdict and returns a code. The text is the part a
    seam needs — which cases differed, and on what — so it is captured rather
    than left on stdout for a human to find.
    """
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    out = buf.getvalue()
    sys.stdout.write(out)
    return rc, out


class Proof:
    """Where steps 3-4 got to, as a VALUE.

    The mechanical half reports WHICH STEP failed and what it observed; it
    does not decide what to do about it, and it does not know that a caller
    might ask an agent.  That judgement belongs to the driver: this module is
    the mechanical half, and a seam is the driver's vocabulary, not its own.

    *step* is one of:

      ``'A'``          the protocol does not replay the project — a defect in
                       the EXTRACTOR, not a fact about the project;
      ``'plan'``       the captured plan is not executable (the capture was
                       missing a declaration it cannot derive);
      ``'ref'``        the reference run captured no goldens;
      ``'B-project'``  the plan is not green on the project's OWN build — the
                       project itself may need a patch;
      ``'B-flat'``     the plan is green on the project but not on the replay
                       — the protocol and the build disagree.
    """
    __slots__ = ('ok', 'step', 'detail', 'columns', 'language')

    def __init__(self, ok: bool, step: str = '', detail: str = '',
                 columns: 'dict | None' = None, language: str = '') -> None:
        self.ok = ok
        self.step = step
        self.detail = detail
        self.columns = columns or {}
        self.language = language

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def __repr__(self) -> str:
        return (f'Proof(ok={self.ok!r}, step={self.step!r}, '
                f'detail={self.detail!r})')

    def __eq__(self, other):
        if isinstance(other, Proof):
            return (self.ok, self.step, self.detail) == (
                other.ok, other.step, other.detail)
        if isinstance(other, int):
            # `prove_legal` used to return an exit code directly.  Kept so a
            # caller comparing against 0/1 still reads correctly.
            return self.exit_code == other
        return NotImplemented

    def __hash__(self):
        return hash((self.ok, self.step, self.detail))


def _measured_drops(plan, env, work: Path) -> list:
    """The volatile line shapes from two reference runs, as `drop` patterns.

    The DETERMINISTIC half of "how are two runs legitimately compared".  Once
    the verification is known to COMPARE captured output — which is what a
    `reference_run` golden means — the lines that moved between two runs of
    one binary ARE the volatile lines, and that is a measurement, not a
    judgement: no agent is asked.  A second run is made under a shifted `TZ`
    so calendar-derived lines move too.

    Returns [] when nothing undeclared moved, or when the measurement cannot
    run at all (a plan that will not load, a run that captures nothing).  A
    failure here is not a verdict about the project — the seam remains the
    fallback for whatever a measurement cannot reach.
    """
    from protocolize import volatiles
    try:
        root_a, root_b = volatiles.capture_twice(plan, env, work,
                                                 shift_date=True)
        modes = volatiles.declared_modes(plan)
        left = volatiles.uncovered(root_a, root_b, modes,
                                   slack=volatiles.declared_slack(plan))
    except Exception as exc:                      # noqa: BLE001 — see above
        print(f'  the volatile measurement could not run ({exc})',
              file=sys.stderr)
        return []
    # A shape beginning `<` is a line-COUNT mismatch, not a line shape: a
    # difference the rules did not account for is a different kind of thing,
    # and a `drop` pattern cannot express it.  It is left for the seam.
    return [volatiles.drop_pattern(v.shape) for v in left
            if not v.shape.startswith('<')]


def _with_drops(plan, drops: list):
    """*plan* with *drops* appended to every comparison assertion's `drop`.

    A rule is added to the assertions that COMPARE text; a plan whose
    verification is not a comparison has none, and nothing is added.
    """
    out = []
    for a in plan.assertions:
        a = dict(a)
        if a.get('kind') in ('text_lockstep', 'field_assertion'):
            a['drop'] = list(a.get('drop') or []) + list(drops)
        out.append(a)
    plan.assertions = tuple(out)
    return plan


def prove_legal(dest: Path, src: Path, build: Path, number: int,
                src_name: str, remedies: 'list | None' = None,
                facts: 'Path | dict | None' = None,
                declaration: Path | None = None,
                notes: str = 'fetched from source') -> Proof:
    """Steps 3-4: the pipeline.  Returns what happened; records nothing.

    Recording is the CALLER's, because a caller that can open a seam may
    repair a failure and reach a verdict — and a row written here would
    report the intermediate failure as the outcome.

    *notes* says what THIS admission is.  A re-prove passes nothing, so the
    row keeps the description it already had instead of every replay
    relabelling an in-tree sample as "fetched from source".
    """
    from protocolize.onboard.extract import (ExtractionError,
                                             capture_verification,
                                             extract_and_prove)
    from protocolize.verification_plan import VerificationPlan
    from protocolize.verify import cli

    work = dest / '.work'
    try:
        proto, report = extract_and_prove(build, tree_root=src, work_root=work)
    except ExtractionError as exc:
        return Proof(False, 'A',
                     '§A failed (the protocol does not replay the project):\n'
                     + str(exc),
                     {'build_protocol_a': 'no', 'verify_on_project_b': 'no',
                      'verify_on_replay_b': 'no'})
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
        return Proof(False, 'plan',
                     'the captured plan is not executable:\n  '
                     + '\n  '.join(plan.problems()),
                     {'build_protocol_a': 'yes', 'verify_on_project_b': 'no',
                      'verify_on_replay_b': 'no', 'status': 'FAIL_B_PLAN'},
                     lang)
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
            return Proof(False, 'ref', 'reference run captured no goldens',
                         {'build_protocol_a': 'yes',
                          'verify_on_project_b': 'no',
                          'verify_on_replay_b': 'no',
                          'status': 'FAIL_B_REF'}, lang)
        print(f'  captured {goldens} goldens from the reference run')
        # The verification COMPARES captured output, so MEASURE which lines
        # two runs disagree on and declare them — unconditionally, before the
        # verdict, never waiting for a failure to ask.  Waiting was wrong: a
        # comparison whose volatile lines happen to match (two runs inside one
        # second) passes with NO rules at all, so the failure that would have
        # provoked the measurement never came, and the plan kept `drop: []`
        # while the next minute's run failed on every case.  Measured.
        drops = _measured_drops(plan, env, dest / '.work' / 'volatiles')
        if drops:
            _with_drops(plan, drops)
            plan.save(dest / 'verification_plan.json')
            print(f'  measured {len(drops)} volatile line shape(s) from two '
                  f'runs; declared as `drop` rules')

    rc, out = _run_plan(cli, [str(dest), '--tree', str(src),
                              '--origin', str(origin)])
    if rc != 0:
        # WHAT failed travels with the verdict.  Measured: a §B failure was
        # reported to the agent as nothing but "the project's own build (exit
        # 1)", so it could not tell a build that needed porting from 132 cases
        # differing on a timestamp — and it answered with patches for the
        # former.  A seam asked to fix something must be told what is wrong.
        return Proof(False, 'B-project',
                     f"§B failed on the project's own build (exit {rc})\n"
                     + out[-4000:],
                     {'build_protocol_a': 'yes', 'verify_on_project_b': 'no',
                      'verify_on_replay_b': 'no', 'status': 'FAIL_B_REF'},
                     lang)
    print("  §B: green on the project's own build")

    flat = work / 'tree'
    proto.save(flat / 'protocol.json')
    shutil.copy2(dest / 'verification_plan.json',
                 flat / 'verification_plan.json')
    rc, out = _run_plan(cli, [str(flat), '--tree', str(flat),
                              '--origin', str(origin)])
    if rc != 0:
        return Proof(False, 'B-flat',
                     f'§B failed on the REPLAY (exit {rc})\n' + out[-4000:],
                     {'build_protocol_a': 'yes', 'verify_on_project_b': 'yes',
                      'verify_on_replay_b': 'no', 'status': 'FAIL_B_FLAT'},
                     lang)
    print('  §B: green on the replay — the two verdicts agree')

    shutil.rmtree(work, ignore_errors=True)
    columns = dict(language=lang, native='yes', build_protocol_a='yes',
                   verify_on_project_b='yes', verify_on_replay_b='yes',
                   status='PASS')
    # Only a FIRST admission gets to label the row.  A re-prove reached the
    # same verdict about the same project, so it must not overwrite what the
    # row says the project IS — measured: replaying a tracked in-tree sample
    # relabelled it "fetched from source", which is false about it.
    if notes:
        columns['notes'] = notes
    return Proof(True, 'legal', 'LEGAL', columns, lang)


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
