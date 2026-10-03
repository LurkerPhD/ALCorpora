"""The one mechanism: a verification plan goes in, verdicts come out.

Replaces, for all three corpora at once:

* the run command and its arguments (``bench_argv``),
* "is the origin usable" (``bench_origin_ready``),
* "capture the origin" (``bench_save_origin``),
* and the per-corpus 700~800-line scripts' harness halves.

It knows nothing about any corpus.  Everything specific arrives in the plan:
which product is under test, where the cases come from, what to run, what the
run must leave behind, what to compare it against, and what must have
happened for the run to count.  See :mod:`protocolize.verification_plan`.

THE ONE RULE THAT UNIFIES THE ORIGIN.  A corpus either has an external truth
(a golden — for the golden-file corpora the only truth available is the
reference build's own output) or it verifies itself (LAPACK's drivers compute
their own residuals).  Both are served by one sentence: **run the reference
instance through this same plan, and keep what the plan calls golden.** The
reference and the candidate therefore traverse the same code path, and "a
failed reference run records nothing" is true by construction rather than
remembered by each corpus.

WHAT IT PRINTS is the interface the harness reads (``verify.counts.BenchCounts``
and ``the runner's verdict`` both parse this text), so it is one
implementation with one spelling:

    CRASHED <id> (t s) / TIMEOUT <id> (t s > budget s)
    FAIL    <id> (n diffs, t s)
    PASS    <id> (t s)
    PASS: n, FAIL: m, TOTAL: t
    Benchmark Total CPU time: X.XXXs

The counts line is NEVER colour-wrapped: it is the machine-readable line, and
MYSTRAN wrapping its own in an escape is why that corpus reported zero
passing cases for as long as it did.
"""

from __future__ import annotations

import json
import os
import re
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from protocolize.private_protocol import expand_tree

from . import compare


class PlanRunError(ValueError):
    """A plan this mechanism cannot execute."""


# ── what the run is given ─────────────────────────────────────

@dataclass
class Env:
    """The run-time roots, handed in by the harness.

    Deliberately NOT the same thing as the plan's ``${TREE}``: that is the
    capture tree (where the reference build lives) and this is the tree under
    test.  Conflating them is the silent failure this repo keeps re-learning
    — a path that exists, just not where the plan looks.
    """

    target: Path                  # the product under test, or the built tree
    tree: Path                    # the tree under test
    origin: Path                  # where the golden / reference lives
    results: Path                 # where this run may leave artifacts
    jobs: int = 1
    cases_root: Path | None = None   # overrides a plan's relative case root
    protocol_tests: list | None = None   # for cases.kind=build_system_tests
    home: Path | None = None      # a per-run scratch root the plan may stage into


@dataclass
class Case:
    id: str
    input: Path
    work: Path
    produced: dict[str, Path] = field(default_factory=dict)
    golden: Path | None = None
    deck_rel: str = ''
    deck_out: str = ''
    env: dict = field(default_factory=dict)


@dataclass
class CaseResult:
    id: str
    verdict: str                  # pass | diff | timeout | crashed | slow
    seconds: float = 0.0
    detail: str = ''
    diffs: list = field(default_factory=list)
    cpu: float = 0.0
    stdout: str = ''


@dataclass
class SuiteResult:
    results: list[CaseResult] = field(default_factory=list)
    counts: object = None
    ok: bool = False
    output: str = ''
    captured: int = 0
    #: The Case objects this run actually executed, by id.  `case.produced`
    #: is bound when the case RUNS (and a restart chain binds it to the
    #: chain's own work dir), so a goldens capture that re-discovers cases
    #: instead of reading these gets an EMPTY `produced` and silently skips
    #: every case it needed.  Handing them back is the difference between
    #: capturing a chain member's output and reporting "no golden".
    cases: dict[str, Case] = field(default_factory=dict)

    def by_id(self, case_id: str) -> CaseResult | None:
        for r in self.results:
            if r.id == case_id:
                return r
        return None


# ── bindings ──────────────────────────────────────────────────

def _bindings(env: Env, case: Case | None = None) -> dict[str, str]:
    out = {
        'target': str(env.target),
        'tree': str(env.tree),
        'origin': str(env.origin),
        'results': str(env.results),
        'jobs': str(env.jobs),
        'scratch': str(Path.cwd()),
        # NOT `env.results / '_home'`.  `{home}` is handed to the corpus as an
        # ABSOLUTE path that the corpus stores in a fixed-length buffer, and
        # `results` already ends in `benchmark_results`.  Measured on NASTRAN:
        # `rfdir` is `character*80` and built as `home // '/' // 'dmap/rf'`,
        # so a home of 73 characters overflowed it by one; the REFERENCE build
        # then could not open its own scratch, and every golden it captured
        # was the RFOPEN failure report instead of the run's output (124 of
        # 132 cases).  `{tree}` is one level up, which keeps the longest
        # required path (`home/<case_id>/scratch`, chains included) inside 80.
        'home': str(env.home or env.tree / '_home'),
    }
    if case is not None:
        out.update({
            'work': str(case.work),
            'case_id': case.id,
            'input.name': case.input.name,
            # The corpus's spelling of the id, so an artifact named after the
            # case matches the name the corpus gave it.
            'input.stem': case.input.stem or case.id,
        })
        if case.deck_out:
            out['deck_rel'] = case.deck_rel
            out['deck_out'] = case.deck_out
            # The deck's own relative path WITHOUT its suffix.  A corpus that
            # saves its report under a different extension than it reads the
            # deck from needs the stem, not the path: `foo.bdf` -> `foo.F06`,
            # where `{deck_rel}` would give `foo.bdf.F06`.
            out['deck_stem'] = str(Path(case.deck_rel).with_suffix(''))
        for name, path in case.produced.items():
            out[name] = str(path)
        if case.golden is not None:
            out['golden'] = str(case.golden)
    return out


def resolve(text: str, env: Env, case: Case | None = None) -> str:
    """Spell *text* against the run: ``${TREE}`` first, then the bindings.

    A ``{name}`` that is not a known binding is left alone rather than
    silently emptied — an unresolved binding is a plan that asked for
    something the mechanism does not have, and an empty path is the failure
    that names nothing.
    """
    spelled = expand_tree(str(text), env.tree) if '$' in str(text) else str(text)
    values = _bindings(env, case)
    for key in sorted(values, key=len, reverse=True):
        spelled = spelled.replace('{' + key + '}', values[key])
    return spelled



# ── a corpus that verifies ITSELF ─────────────────────────────

#: ``ctest -V`` prefixes every line with ``<n>: ``.  Unstripped, a table
#: reader finds nothing and every count reads 0 — a verdict with no checks
#: behind it, which is the one shape a self-verifying corpus must never
#: accept.  Measured on LAPACK before it was stripped.
_CTEST_PREFIX = re.compile(r'^\s*\d+:\s?')

#: The suite's own aggregate row: ANY label, then the check count and the two
#: error counts, each error followed by its percentage.  The label is matched
#: GENERICALLY (any words) rather than enumerated: the vocabulary is the
#: corpus's (LAPACK's is REAL/DOUBLE PRECISION/...), and a code-side list of
#: it is the same defect as naming a corpus.  Which label is the TOTAL row is
#: selected by the plan's `total_label`.
_TABLE_ROW = re.compile(
    r'^\s*(?:-->\s*)?'
    r'([A-Za-z][A-Za-z0-9 ._/-]*?)\s+'
    r'(\d+)\s+(\d+)\s*\(\s*[\d.]+%\)\s+'
    r'(\d+)\s*\(\s*[\d.]+%\)\s*$')

#: A section heading, matched with the CORPUS'S OWN pattern, which the plan
#: carries.  This module may not name a corpus's sections any more than it may
#: name a corpus — hardcoding ``LAPACK|BLAS`` here was caught by the runner's
#: own no-corpus-name guard — and using the corpus's pattern makes the
#: attribution exactly the one the corpus made.
#:
#: The heading is a SEPARATE line from the counts: reading the counts off it
#: was this reader's first bug, and the corpus's own captured sample is what
#: showed it.
def _heading_name(line: str, pattern: re.Pattern) -> str | None:
    m = pattern.match(line.strip())
    if not m:
        return None
    got = [g for g in m.groups() if g]
    return got[0] if got else m.group(0)


#: Which row within a section carries the section's total.  Read from the
#: plan (`total_label`) — it is the corpus's own word.
def read_table(text: str, section_pattern: str,
               total_label: str = '') -> dict:
    """The suite's own tally, per section — from the corpus's real output.

    Two authorities are read out of one run: this aggregate table, which
    counts individual numerical CHECKS, and the runner summary, which counts
    whole TESTS.  Neither subsumes the other — the table cannot see a driver
    that crashed before printing, and the runner does not inspect the checks a
    completed driver reported.

    Only the TOTAL row is taken per section (the plan's `total_label`), which
    is what the corpus's own reader does: the per-precision rows are the
    breakdown of that same total, so summing the section and its rows would
    double it.
    """
    out: dict[str, dict] = {}
    current: str | None = None
    heading = re.compile(section_pattern)
    for raw in text.splitlines():
        line = _CTEST_PREFIX.sub('', raw).rstrip()
        name = _heading_name(line, heading)
        if name is not None:
            current = name
            out.setdefault(current, {'run': 0, 'numerr': 0, 'otherr': 0})
            continue
        m = _TABLE_ROW.match(line)
        if m and current is not None and total_label and total_label in m.group(1):
            out[current]['run'] += int(m.group(2))
            out[current]['numerr'] += int(m.group(3))
            out[current]['otherr'] += int(m.group(4))
    return out


def _read_pattern(text: str, pattern: str) -> dict:
    """Every named group of every match, as ints where they are numeric."""
    got: dict = {}
    for m in re.finditer(pattern, text):
        for k, v in m.groupdict().items():
            if v is None:
                continue
            try:
                got[k] = int(v)
            except ValueError:
                try:
                    got[k] = float(v)
                except ValueError:
                    got[k] = v
    return got


def check_self_reports(plan, suite_stdout: str) -> list[str]:
    """Every reason the suite's own report does not support a pass.

    Empty means the report was read AND met its floors.  The floors are the
    point: a self-verifying corpus's whole verdict comes from here, so a
    report that could not be read must fail rather than pass vacuously.
    """
    problems: list[str] = []
    for i, a in enumerate(plan.assertions):
        if a.get('kind') != 'self_report':
            continue
        reader = a.get('reader') or {}
        if a.get('section_pattern'):
            got = read_table(suite_stdout, a['section_pattern'],
                             a.get('total_label', ''))
            for section, counts in got.items():
                for key, floor in (a.get('require_min') or {}).items():
                    if counts.get(key, 0) < floor:
                        problems.append(
                            f'assertions[{i}] {section}.{key} is '
                            f'{counts.get(key, 0)}, below the required '
                            f'{floor} — a report that could not be read is '
                            f'not a pass')
                for key in a.get('require_zero', ()) or ():
                    if counts.get(key, 0):
                        problems.append(
                            f'assertions[{i}] {section}.{key} is '
                            f'{counts[key]}, required to be zero')
        elif reader.get('pattern'):
            got = _read_pattern(suite_stdout, reader['pattern'])
            if not got:
                problems.append(
                    f'assertions[{i}] the suite report matched nothing: a '
                    f'verdict with no checks behind it is not a pass')
            for key, floor in (a.get('require_min') or {}).items():
                if got.get(key, 0) < floor:
                    problems.append(
                        f'assertions[{i}].{key} is {got.get(key, 0)}, below '
                        f'the required floor {floor}')
            for key in a.get('require_zero', ()) or ():
                if got.get(key, 0):
                    problems.append(
                        f'assertions[{i}].{key} is {got[key]}, required to be '
                        f'zero')
        else:
            problems.append(
                f'assertions[{i}] is a self_report with no reader: nothing '
                f'says which part of the report to believe')
    return problems


# ── metrics against a stored reference ────────────────────────

REFERENCE_JSON = 'reference.json'


def _statistic(values, statistic: str) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    if statistic == 'median':
        mid = len(vals) // 2
        return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    return sum(vals) / len(vals)


def record_reference(origin: Path, metrics: dict, *, runs: int = 1,
                     statistic: str = 'median') -> None:
    """Persist a metric's reference underneath *origin*.

    The corpus's own script recorded a five-run median for LAPACK and, on the
    only production path, never wrote the file at all — the hook it lived in
    was handed a ``None`` build dir.  Recording here means the criterion
    actually has a reference to compare against.
    """
    origin = Path(origin)
    origin.mkdir(parents=True, exist_ok=True)
    stored: dict = {}
    path = origin / REFERENCE_JSON
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding='utf-8'))
        except ValueError:
            stored = {}
    for name, value in metrics.items():
        vals = value if isinstance(value, (list, tuple)) else [value]
        stored[name] = {'statistic': statistic, 'runs': runs or len(vals),
                        'value': _statistic(vals, statistic)}
    path.write_text(json.dumps(stored, indent=2, sort_keys=True) + '\n',
                    encoding='utf-8')


def reference_value(origin: Path, metric: str) -> float | None:
    """The stored reference for *metric*, or ``None`` if none was recorded."""
    path = Path(origin) / REFERENCE_JSON
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return None
    entry = stored.get(metric)
    if isinstance(entry, dict):
        return entry.get('value')
    return entry if isinstance(entry, (int, float)) else None


def compare_metric(origin: Path, metric: str, value: float, *,
                   ratio: float) -> list[str]:
    """Whether *value* is within *ratio* of the stored reference.

    A metric with NO reference is not "fine": it is a criterion that cannot be
    evaluated, and reporting that is the honest outcome.
    """
    ref = reference_value(origin, metric)
    if ref is None:
        return [f'metric {metric!r} has no reference under {origin} — a '
                f'criterion with nothing to compare against was not applied']
    if ref <= 0:
        return [f'metric {metric!r} has a reference of {ref}, which no ratio '
                f'can be taken against']
    got = value / ref
    if got > ratio:
        return [f'metric {metric!r} is {value:.4f} against a reference of '
                f'{ref:.4f} (ratio {got:.4f} > {ratio})']
    return []


def reduce_metric(root: Path, *, reduce: str, pattern: str,
                  name: str = '') -> float:
    """One number, reduced from lines the run's artifacts carry.

    A different primitive from a value the suite prints: this is a reduction
    OVER what the run produced (LAPACK's per-driver ``Total time used`` lines
    live in the drivers' own logs).
    """
    values: list[float] = []
    root = Path(root)
    if not root.is_dir():
        return 0.0
    rx = re.compile(pattern)
    for f in sorted(root.rglob('*')):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        values += [float(m) for m in rx.findall(text)]
    if reduce == 'sum':
        return float(sum(values))
    if reduce in ('median', 'mean'):
        return _statistic(values, reduce)
    raise PlanRunError(f'reduce {reduce!r} is not one this mechanism knows')


# ── discovery ─────────────────────────────────────────────────

def _root(plan_root: str, env: Env) -> Path:
    if env.cases_root is not None:
        return Path(env.cases_root)
    return Path(resolve(plan_root, env))


def discover(plan, env: Env) -> list[Case]:
    """The cases the plan declares, in the plan's own order."""
    spec = plan.cases or {}
    kind = spec.get('kind')
    root = _root(spec.get('root', ''), env)

    if kind == 'one_per_dir':
        return _discover_one_per_dir(spec, root, env, plan)
    if kind == 'case_list':
        return _discover_case_list(spec, root, env, plan)
    if kind == 'build_system_tests':
        return _discover_registered(spec, env, plan)
    raise PlanRunError(f'cases.kind {kind!r} is not one this mechanism runs')


def _make_case(case_id: str, input_path: Path, env: Env, plan,
               deck_rel: str = '') -> Case:
    """A case, with the plan's ``produced`` and ``golden`` spelled out.

    Every discovery path returns through here, so a case always carries its
    own resolved output and reference paths.  Building a bare ``Case`` in one
    discovery branch is how a binding goes unspelled and every comparison
    then looks for a file literally named ``{out}``.
    """
    case = Case(id=case_id, input=input_path,
                work=env.results / 'work' / case_id)
    if deck_rel:
        # Set BEFORE the templates are spelled: an artifact mirrors the
        # deck's own relative path (`Benchmark/bar_01.DAT` ->
        # `Benchmark/bar_01.out`), and a binding assigned after the fact is a
        # binding the plan's `{deck_out}` never saw.
        case.deck_rel = deck_rel
        case.deck_out = str(Path(deck_rel).with_suffix('.out'))
    return rebind(plan, env, case)


def rebind(plan, env: Env, case: Case) -> Case:
    """Spell the plan's paths for *case* against its CURRENT work dir.

    Split out because a case's work dir can change after it is built: a
    restart chain's prerequisites run in the chain's own directory, and their
    artifacts must follow.  Bound only once, a prerequisite writes to a path
    that the chain never created, the program fails on its very first write,
    and the chain reports a failure that is the harness's (measured: the
    chain never got as far as its first prerequisite).
    """
    bind = _bindings(env, case)
    bind.setdefault('golden', '')
    case.produced.clear()
    for entry in plan.produced:
        # A name that spells itself against ``{work}`` (or ``{results}``, or
        # an absolute path) is taken as written; a bare relative name is
        # results-relative.  The distinction matters because a chained case's
        # work dir is not where discovery first put it: a plan that hand-wrote
        # the work layout (`work/{case_id}/{input.stem}.out`) keeps pointing at
        # the directory the case USED to be in, and its artifacts then land
        # outside the chain that runs it.
        spelled = _spell(str(entry['name']), bind)
        path = Path(spelled)
        # Rooted already if the template named a root binding (`{work}`,
        # `{results}`, ...) or is absolute.  Judging by `is_absolute()` alone
        # is wrong: a build dir given as a RELATIVE path makes every spelled
        # binding relative too, and the name then gets the results root
        # prefixed a second time — measured with the CLI, which takes the
        # build dir exactly as the caller typed it.
        rooted = path.is_absolute() or \
            spelled.startswith(str(env.results) + '/') or \
            spelled == str(env.results)
        case.produced[entry.get('as', 'out')] = \
            path if rooted else Path(env.results) / path
    if plan.golden.source == 'reference_run' and plan.golden.path:
        case.golden = Path(_spell(
            str(Path(resolve(plan.golden.root, env)) / plan.golden.path), bind))
    bind['golden'] = str(case.golden or '')
    return case


def _spell(template: str, bindings: dict[str, str]) -> str:
    out = str(template)
    for key in sorted(bindings, key=len, reverse=True):
        out = out.replace('{' + key + '}', bindings[key])
    return out



def _id_spelling(case_id: str, spec: dict | None) -> str:
    """The case id in the CORPUS's own spelling.

    A corpus names its case directories one way and the inputs inside them
    another: NASTRAN's directory is ``D01-00-0A`` and its input is
    ``d01000a.inp``.  The spelling is part of the declaration — inferred from
    nothing — which is what makes ``{stem}`` usable in both the input's name
    and the artifact's.  Measured: a captured plan that said ``{id}.inp``
    discovered zero cases for that whole corpus.
    """
    out = case_id
    spec = spec or {}
    if spec.get('lower'):
        out = out.lower()
    for ch in spec.get('remove', '') or '':
        out = out.replace(ch, '')
    return out


def _find_input(case_dir: Path, case_id: str,
                inp_spec: dict) -> Path | None:
    """The case's input file, or ``None`` when the corpus's rule finds none.

    ``None`` is a real answer: the corpus's own discovery SKIPS a directory
    whose input is absent (NASTRAN: 132 of 133 directories are cases).  What
    it must not be is a silent skip — the plan says ``skip_without_input`` or
    the gap is kept and `require_min` makes it visible.
    """
    name_t = inp_spec.get('name')
    if not name_t:
        return None
    stem = _id_spelling(case_id, inp_spec.get('id_spelling'))
    cand = case_dir / name_t.replace('{stem}', stem).replace('{id}', case_id)
    if cand.is_file():
        return cand
    globbed = sorted(case_dir.glob(name_t.replace('{stem}', '*')
                                   .replace('{id}', '*')))
    return globbed[0] if globbed else None


def _discover_one_per_dir(spec, root: Path, env: Env, plan) -> list[Case]:
    """A case per entry under *root*: a directory (NASTRAN), or a file.

    The NASTRAN shape is one directory per case with a fixed-named input
    inside it; a flat directory of inputs is the same declaration with the
    stem as the id.
    """
    inp_spec = spec.get('input') or {}
    id_from = (spec.get('id') or {}).get('from', 'dirname')
    exclude = set(spec.get('exclude') or ())
    skip_missing = bool(spec.get('skip_without_input'))
    out: list[Case] = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if entry.name in exclude or entry.name.startswith('.'):
            # A directory the corpus declares is not a case: discovering it
            # would invent a case the corpus never declared, with no golden.
            continue
        if entry.is_dir():
            case_id = entry.name
            inp = _find_input(entry, case_id, inp_spec)
            if inp is None and skip_missing:
                continue
            inp = inp or entry / (inp_spec.get('name') or '{id}')
        elif entry.is_file():
            case_id = entry.stem if id_from == 'stem' else entry.name
            inp = entry
        else:
            continue
        # A declared case whose input is not there is NOT skipped: it is a
        # gap, and skipping is how a missing input became a silent pass.
        out.append(_make_case(case_id, inp, env, plan))
    return out


def _discover_case_list(spec, root: Path, env: Env, plan) -> list[Case]:
    """A case list file with an INCLUDE chain and per-record fields.

    The corpus's own format decides the columns; the plan maps them
    (``record``/``group_by``), so a corpus whose list gained a column is a
    plan change rather than a parser change.
    """
    rec = spec.get('record') or {}
    sep = rec.get('sep', ';')
    comment = rec.get('comment', '#')
    include = (spec.get('include') or {}).get('directive', 'INCLUDE')
    files = spec.get('files') or []
    accept = set((rec.get('types') or {}).get('accept') or ())
    type_field = int((rec.get('types') or {}).get('field', 0))
    deck_field = int((rec.get('deck') or {}).get('field', 1))

    lines: list[str] = []

    def _read(path: Path) -> None:
        if not path.is_file():
            raise PlanRunError(f'the case list {path} is not there')
        for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
            line = raw.strip()
            if not line or line.startswith(comment):
                continue
            if line.upper().startswith(include.upper() + ' '):
                parts = line.split()
                if len(parts) >= 2:
                    _read(path.parent / parts[1])
                continue
            lines.append(line)

    for f in files:
        _read(root / f)

    deck_root = _root(spec.get('deck_root') or spec.get('root', ''), env)
    seen: dict[str, Case] = {}
    for line in lines:
        fields = [f.strip() for f in line.split(sep)]
        if len(fields) <= max(type_field, deck_field):
            continue
        if accept and fields[type_field] not in accept:
            continue
        deck = fields[deck_field].replace('\\', '/')
        if deck in seen:
            continue
        case_id = Path(deck).stem
        seen[deck] = _make_case(case_id, deck_root / deck, env, plan, deck)
    return [seen[k] for k in sorted(seen)]


def _discover_registered(spec, env: Env, plan) -> list[Case]:
    """The build system's own registry (``ctest --show-only=json-v1``).

    There is no per-case input to stage: the registrations carry their own
    commands, which is what ``run.kind == 'suite'`` hands to the build
    system's runner.
    """
    tests = env.protocol_tests or []
    return [_make_case(t.get('name', f'case{i}'), Path(), env, plan)
            for i, t in enumerate(tests)]


# ── staging ───────────────────────────────────────────────────

def apply_staging(plan, env: Env, case: Case) -> dict[str, str]:
    """Perform the corpus's declared preparation; return the environment.

    This is what "the corpus-side preparation must be complete" means in
    practice, and it is not cosmetic.  Measured on NASTRAN: without it the
    program exits 0 and produces NOTHING, so all 132 cases fail with "no
    output" — a result that reads as a broken transpiler and is actually a
    missing preparation.

    The vocabulary is closed and generic.  Each op is a thing a corpus
    genuinely has to do to make its program runnable:

    ``env``        set an environment variable for the case's process
    ``mkdir``      create a directory the program will write into
    ``symlink``    point a path at something the corpus ships
    ``substitute`` write a copy of a shipped file with text replaced

    ``substitute`` is the fragile one and defaults to ``required``: its
    search text is a literal against a file the corpus ships, so a corpus
    that renames or reflows that file must fail loudly rather than proceed
    with a config that no longer says what it said.
    """
    extra: dict[str, str] = {}
    for i, op in enumerate(plan.staging):
        kind = op.get('op')
        if kind == 'env':
            extra[op['name']] = resolve(op.get('value', ''), env, case)
        elif kind == 'mkdir':
            Path(resolve(op['path'], env, case)).mkdir(parents=True,
                                                       exist_ok=True)
        elif kind == 'symlink':
            link = Path(resolve(op['link'], env, case))
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.exists():
                link.symlink_to(Path(resolve(op['target'], env, case)))
        elif kind == 'alias':
            _alias(op, env, case)
        elif kind == 'substitute':
            _substitute(op, env, case, i)
        else:
            raise PlanRunError(
                f'staging[{i}].op {kind!r} is not one this mechanism performs')
    return extra


def _alias(op: dict, env: Env, case: Case) -> None:
    """Make each staged file reachable under its uppercase spelling too.

    Some of a corpus's files are opened case-insensitively.  Measured on
    NASTRAN: it ships ``rscards`` and opens ``RSCARDS``, and a run without
    the second spelling reads no restart cards while reporting nothing of its
    own — so the case's output differs from the golden in a way that reads as
    the transpiler's doing.  The reference build needs it too, which is why
    a golden captured without it has no such error to compare against.

    A file that is ALREADY there under the uppercase name is left as it is:
    a corpus shipping both spellings means them as two files.
    """
    root = Path(resolve(op['in'], env, case))
    if not root.is_dir():
        return
    for f in sorted(root.iterdir()):
        upper = f.name.upper()
        if f.name == upper:
            continue
        alias = root / upper
        if alias.exists() or alias.is_symlink():
            continue
        alias.symlink_to(f.name)


def _substitute(op: dict, env: Env, case: Case, index: int) -> None:
    """Write a copy of a shipped file with declared text replaced."""
    src = Path(resolve(op['from'], env, case))
    dst = Path(resolve(op['to'], env, case))
    if not src.is_file():
        raise PlanRunError(f'staging[{index}] reads {src}, which is not there')
    text = src.read_text(encoding='utf-8', errors='replace')
    for pair in op.get('replace', ()):
        old, new = pair[0], resolve(pair[1], env, case)
        if old not in text and op.get('required', True):
            raise PlanRunError(
                f'staging[{index}] was to replace {old!r} in {src}, and that '
                f'text is not in it — the file the corpus ships has changed, '
                f'so this preparation no longer says what it said')
        text = text.replace(old, new)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding='utf-8')


def _stage(plan, env: Env, case: Case) -> None:
    """Put in the work dir whatever the case needs to RUN.

    This is the corpus-side preparation the plan declares, and it is not
    optional: a program run with ``cwd={work}`` and handed a bare input name
    finds nothing there, and one that then falls through to a default branch
    reports a PASS for a run that read no input at all.  Measured exactly
    that way, on this runner's own first version.

    ``from: input_only``  — the case's input, nothing else (MYSTRAN's decks).
    ``from: case_dir``    — every file beside it, minus ``exclude_suffix``
                            (NASTRAN's verification dirs, which carry support
                            decks and the restart state a chain needs).
    """
    spec = plan.inputs or {}
    src = spec.get('from')
    if src not in ('input_only', 'case_dir'):
        return
    case.work.mkdir(parents=True, exist_ok=True)
    if src == 'input_only':
        if case.input.is_file():
            shutil.copy2(str(case.input), str(case.work / case.input.name))
        return
    parent = case.input.parent
    if not parent.is_dir():
        return
    excl = tuple(spec.get('exclude_suffix', ()))
    for f in sorted(parent.iterdir()):
        if not f.is_file() or f.suffix in excl:
            continue
        shutil.copy2(str(f), str(case.work / f.name))


# ── running ───────────────────────────────────────────────────

def _run_process(plan, env: Env, case: Case) -> CaseResult:
    argv = [resolve(tok, env, case) for tok in plan.run.argv]
    cwd = Path(resolve(plan.run.cwd, env, case)) if plan.run.cwd else env.results
    cwd.mkdir(parents=True, exist_ok=True)
    budget = plan.run.case_timeout_s

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True,
                              text=True, timeout=budget,
                              env={**os.environ, **(case.env or {})})
        rc, out = proc.returncode, (proc.stdout or '') + (proc.stderr or '')
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        return CaseResult(case.id, 'timeout', elapsed,
                          f'({elapsed:.1f}s > {budget}s)', [])
    elapsed = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = ((after.ru_utime - before.ru_utime)
           + (after.ru_stime - before.ru_stime))

    if rc < 0 or rc not in tuple(plan.run.exit_accept):
        # A signal is death (rc < 0); an exit code outside the corpus's
        # accepted set is a program that did not complete as the corpus
        # declares completion.  Both are the harness's CRASHED, and which
        # one it was is in the detail.
        why = f'signal {-rc}' if rc < 0 else f'exit {rc}'
        return CaseResult(case.id, 'crashed', elapsed,
                          f'({why}, {elapsed:.1f}s)  {out.strip()[:200]}',
                          stdout=out)
    return CaseResult(case.id, 'pass', elapsed, cpu=cpu, stdout=out)


def _check_assertions(plan, env: Env, case: Case) -> CaseResult | None:
    """``None`` when every assertion holds, else the failing verdict."""
    for a in plan.assertions:
        kind = a.get('kind')
        if kind == 'artifact':
            path = Path(resolve(a['path'], env, case))
            if not path.exists():
                return CaseResult(case.id, 'diff', 0.0,
                                  f'(no {path.name})')
            if a.get('non_empty') and path.stat().st_size == 0:
                return CaseResult(case.id, 'diff', 0.0,
                                  f'({path.name} is empty)')
        elif kind == 'text_lockstep':
            produced = Path(resolve(a['produced'], env, case))
            golden = Path(resolve(a['reference'], env, case))
            if not golden.is_file():
                # NOT a pass.  The old scripts scored a missing golden as
                # "runtime-only PASS", which is how a truncated origin turned
                # a whole corpus green.
                return CaseResult(case.id, 'diff', 0.0,
                                  f'(no golden for {case.id})')
            diffs = compare.lockstep(
                produced.read_text(encoding='utf-8', errors='replace'),
                golden.read_text(encoding='utf-8', errors='replace'),
                drop=a.get('drop', ()), rtol=a.get('rtol', compare.DEFAULT_RTOL),
                atol=a.get('atol', compare.DEFAULT_ATOL))
            if diffs:
                return CaseResult(case.id, 'diff', 0.0,
                                  f'({len(diffs)} diffs)', diffs)
        elif kind == 'metric':
            got = (reduce_metric(Path(resolve(a['from'], env, case)),
                                 reduce=a.get('reduce', 'sum'),
                                 pattern=a['pattern'])
                   if a.get('pattern') else a.get('value', 0.0))
            problems = compare_metric(Path(resolve('{origin}', env, case)),
                                      a['name'], got,
                                      ratio=a.get('ratio', 1.10))
            if problems:
                return CaseResult(case.id, 'diff', 0.0,
                                  '(' + '; '.join(problems) + ')')
        elif kind in ('self_report', 'suite'):
            # Judged ONCE over the whole run, in `_finish`: a corpus's report
            # is about the run, not about one case, and reading it per case
            # would apply the same criterion N times.
            continue
        elif kind == 'field_assertion':
            produced = Path(resolve(a['produced'], env, case))
            golden = Path(resolve(a['reference'], env, case))
            if not golden.is_file():
                return CaseResult(case.id, 'diff', 0.0,
                                  f'(no golden for {case.id})')
            diffs = compare.field_lockstep(
                produced.read_text(encoding='utf-8', errors='replace'),
                golden.read_text(encoding='utf-8', errors='replace'),
                model=a.get('model') or {}, drop=a.get('drop', ()),
                rtol=a.get('rtol', compare.DEFAULT_RTOL))
            if diffs:
                return CaseResult(case.id, 'diff', 0.0,
                                  f'({len(diffs)} diffs)', diffs)
        else:
            raise PlanRunError(f'assertion kind {kind!r} is not one this '
                               f'mechanism knows')
    return None


def _chain_of(plan, case: Case) -> list[str]:
    """The prerequisites this case declares, in order."""
    order = (plan.chains or {}).get('order') or {}
    return list(order.get(case.id, ()))


def _run_chain(plan, env: Env, case: Case, by_id: dict,
               carried: dict) -> CaseResult:
    """Run a case's prerequisites in ONE shared dir, then the case itself.

    Both halves of this are the corpus's own semantics, transcribed:

    * SHARED DIRECTORY.  NASTRAN's SOF subsystem creates interconnected
      database files (POOL, ``.sof1``) that must all reside in the same
      working directory; running a prerequisite separately breaks the
      linkage.
    * HANDOVER UNDER THE NEXT NAME.  ``<prev>.sof1`` is copied to
      ``<next>.sof1`` before that case runs, because the subsystem looks for
      a file named after the CURRENT input's stem.  State left under its old
      name is simply not found, and the case then reports a difference
      (measured: 19 of 132, exactly the chain members).

    Only the FINAL case is judged — a prerequisite's output is scaffolding
    and its golden describes a different deck.
    """
    named = (plan.chains.get('carry') or {}).get('as')
    suffix = Path(case.produced[named]).suffix if named else ''
    prev = None
    for dep_id in _chain_of(plan, case):
        dep = by_id[dep_id]
        dep.work = case.work
        rebind(plan, env, dep)
        if prev is not None and suffix:
            shutil.copy2(str(prev), str(case.work / f'{dep.input.stem}{suffix}'))
        _stage(plan, env, dep)
        dep.env = apply_staging(plan, env, dep)
        r = _run_process(plan, env, dep)
        carried[dep_id] = r
        if r.verdict != 'pass':
            # The final case never runs on state that was never built.
            return CaseResult(case.id, r.verdict, r.seconds,
                              f'(prerequisite {dep_id} {r.verdict})')
        state = case.work / f'{dep.input.stem}{suffix}' if suffix else None
        prev = state if state is not None and state.is_file() else None

    if prev is not None:
        shutil.copy2(str(prev), str(case.work / f'{case.input.stem}{suffix}'))
    _stage(plan, env, case)
    case.env = apply_staging(plan, env, case)
    return _run_process(plan, env, case)


def run(plan, env: Env, only=None) -> SuiteResult:
    """Discover, run, judge.  The plan decides everything.

    *only* limits the run to the named cases.  It exists because attributing
    ONE symptom means running the decks that can show it, not the corpus: the
    gap-extraction flow builds a single mixed stem and compares it against the
    origin, and a full 485-deck suite to answer a question about one stem is
    the difference between a minute and an hour.
    """
    problems = plan.problems()
    if problems:
        raise PlanRunError('the plan is not executable: ' + '; '.join(problems))

    all_cases = discover(plan, env)
    known = {c.id for c in all_cases}
    order = plan.chains.get('order') or {}
    for case_id, deps in order.items():
        if case_id not in known:
            raise PlanRunError(
                f'the plan declares a chain for {case_id!r}, which is not a '
                'case it discovers — a chain whose head does not exist judges '
                'nothing')
        for dep in deps:
            if dep not in known:
                raise PlanRunError(
                    f'the chain for {case_id!r} names {dep!r} as a '
                    'prerequisite, which is not a case this plan discovers')

    cases = all_cases
    if only:
        # A filter selects a case AND its prerequisites: asking for the
        # restart that depends on a checkpoint means running the checkpoint,
        # and a selection that dropped it would fail the case for a reason
        # the filter created.
        wanted = set(only)
        frontier = [c for c in wanted if c in order]
        while frontier:
            for dep in order.get(frontier.pop(), ()):
                if dep not in wanted:
                    wanted.add(dep)
                    frontier.append(dep)
        unknown = sorted(wanted - known)
        if unknown:
            raise PlanRunError(
                f'{unknown} are not cases this plan discovers — a filter that '
                f'matches nothing would run an empty suite and report it clean')
        cases = [c for c in all_cases if c.id in wanted]
    if env.results.exists():
        shutil.rmtree(env.results)
    env.results.mkdir(parents=True, exist_ok=True)

    results: list[CaseResult] = []
    suite_stdout = ''
    suite_cpu: float | None = None
    if plan.run.kind == 'suite':
        results, suite_stdout, suite_cpu = _run_suite(plan, env, cases)
    else:
        by_id = {c.id: c for c in cases}
        # A chained case and its prerequisites share ONE directory, named for
        # the case — the final one — so two chains cannot collide.  Only the
        # cases that actually run there get a directory: a prerequisite is
        # run by its chain, never on its own, and creating a work dir for it
        # would leave the empty directory of a run that never happened.
        for case in cases:
            if _chain_of(plan, case):
                case.work = env.results / 'work' / f'chain_{case.id}'
                rebind(plan, env, case)
                case.work.mkdir(parents=True, exist_ok=True)

        judged: dict[str, CaseResult] = {}
        carried: dict[str, CaseResult] = {}
        for case in cases:
            if not _chain_of(plan, case):
                continue
            res = _run_chain(plan, env, case, by_id, carried)
            if res.verdict == 'pass':
                failed = _check_assertions(plan, env, case)
                if failed is not None:
                    failed.seconds = res.seconds
                    res = failed
            if not res.detail:
                res.detail = f'({res.seconds:.1f}s)'
            judged[case.id] = res

        for case in cases:
            if case.id in judged:
                continue
            if case.id in carried:
                # It ran as some chain's prerequisite, and reporting it is
                # all that is left: running it again would be a second
                # execution of a case the plan already placed in a chain.
                judged[case.id] = carried[case.id]
                continue
            case.work.mkdir(parents=True, exist_ok=True)
            _stage(plan, env, case)
            case.env = apply_staging(plan, env, case)
            res = _run_process(plan, env, case)
            # A per-case run's own output is where such a corpus states its
            # report, exactly as a suite's is: collecting it here is what lets
            # `self_report` judge either shape without a second code path.
            suite_stdout += getattr(res, 'stdout', '') or ''
            if res.verdict == 'pass':
                failed = _check_assertions(plan, env, case)
                if failed is not None:
                    failed.seconds = res.seconds
                    res = failed
            if not res.detail:
                res.detail = f'({res.seconds:.1f}s)'
            judged[case.id] = res

        results = [judged[c.id] for c in cases]

    return _finish(plan, results, env, suite_stdout=suite_stdout,
                   cpu=suite_cpu, cases=cases)


def _run_suite(plan, env: Env, cases: list[Case]) -> list[CaseResult]:
    """Hand the whole set to the build system's runner in one invocation.

    The registration is where the cases come from, so there is nothing to
    iterate: the runner reports per-test outcomes and this reads them.
    """
    argv = [resolve(tok, env, cases[0] if cases else None)
            for tok in plan.run.argv]
    cwd = Path(resolve(plan.run.cwd, env)) if plan.run.cwd else Path(env.target)
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                          timeout=max(plan.run.case_timeout_s * max(len(cases), 1),
                                      60))
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    elapsed = time.monotonic() - started
    # The suite's CPU is the invoked runner's own (user+sys), so it is
    # load-immune the way the per-case measurement is.  Measured here because
    # this path makes exactly ONE invocation: without it the reported total
    # was 0.000s — a number nothing measured, printed into the line the
    # performance comparison reads.
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    out = (proc.stdout or '') + (proc.stderr or '')
    return _parse_suite(plan, out, proc.returncode, elapsed, cases), out, cpu


#: The suite runner's own per-test vocabulary, as the build systems print it.
_SUITE_FAIL_RE = re.compile(r'^\s*\d+\s*-\s*(\S+)\s*\(Failed\)', re.M)
_SUITE_FAILED_RE = re.compile(r'^\s*Failed\s+(\S+)\s*$', re.M)


def _parse_suite(plan, out: str, rc: int, elapsed: float,
                 cases: list[Case], cpu: float = 0.0) -> list[CaseResult]:
    """One result per registered case, judged by the plan's suite assertions."""
    declared = [c.id for c in cases]
    if rc != 0 and rc not in tuple(plan.run.exit_accept):
        return [CaseResult(cid, 'crashed', elapsed, f'(suite exited {rc})')
                for cid in declared]
    failed = set(_SUITE_FAIL_RE.findall(out)) | set(_SUITE_FAILED_RE.findall(out))
    results: list[CaseResult] = []
    for cid in declared:
        if cid in failed:
            results.append(CaseResult(cid, 'diff', elapsed,
                                      '(reported failed)'))
        else:
            results.append(CaseResult(cid, 'pass', elapsed))
    return results


# ── judging ───────────────────────────────────────────────────

def _finish(plan, results: list[CaseResult], env: Env,
            captured: int = 0, suite_stdout: str = '',
            cpu: float | None = None,
            cases: list[Case] | None = None) -> SuiteResult:
    counts = _count(results)

    # The suite-level assertions judge the RUN, not a case: the corpus's own
    # summary table is what a self-verifying corpus's verdict actually rests
    # on, and it is read from the runner's stdout.  A failure here is reported
    # against the suite's own id, because that is what failed to hold.
    complaint = check_self_reports(plan, suite_stdout) if suite_stdout else []
    if complaint:
        results = list(results) + [
            CaseResult(plan.target.product or 'suite', 'diff', 0.0,
                       '(' + '; '.join(complaint) + ')')]
        counts = _count(results)

    ok = _meets(plan, results, counts)
    suite = SuiteResult(results=results, counts=counts, ok=ok,
                        captured=captured,
                        cases={c.id: c for c in (cases or ())})
    # A suite's CPU is the ONE invocation it made; a per-case run's is the sum
    # of its cases.  Both are measurements; neither is a default.
    suite.output = render(results, counts,
                          cpu_total=_cpu_total(results) if cpu is None else cpu)
    return suite


def _count(results: list[CaseResult]):
    from .counts import BenchCounts
    c = BenchCounts()
    for r in results:
        if r.verdict == 'pass':
            c.p += 1
        elif r.verdict == 'diff':
            c.diff += 1
        elif r.verdict == 'timeout' or r.verdict == 'slow':
            c.timeout += 1
        elif r.verdict == 'crashed':
            c.crash += 1
    c.total = len(results)
    return c


def _cpu_total(results: list[CaseResult]) -> float:
    return sum(getattr(r, 'cpu', 0.0) or 0.0 for r in results)


def _meets(plan, results: list[CaseResult], counts) -> bool:
    """The plan's own requirements, which is where non-vacuity lives.

    ``require_zero`` is the per-kind absence; ``require_min`` is what must
    HAVE happened.  A plan cannot reach here without a ``require_min`` —
    ``problems()`` refuses it — so a run whose results nobody could see is
    never green.
    """
    for kind in plan.summary.get('require_zero', ()) or ():
        n = {'diff': counts.diff, 'crash': counts.crash,
             'timeout': counts.timeout, 'slow': 0}[kind]
        if n:
            return False
    have = {'pass': counts.p, 'diff': counts.diff, 'crash': counts.crash,
            'timeout': counts.timeout, 'slow': 0,
            'total': counts.total}
    for key, floor in (plan.summary.get('require_min') or {}).items():
        if have.get(key, 0) < floor:
            return False
    return True


# ── one printer ───────────────────────────────────────────────

def render(results: list[CaseResult], counts, cpu_total: float = 0.0) -> str:
    """The text the harness reads, printed by exactly one place.

    The counts line carries no colour: it is the machine-readable line, and a
    colour escape on it is what made MYSTRAN's passing count parse as zero.

    ``FAIL`` is spelled for BOTH a value difference and a missing artifact:
    the harness's ``extract_fail_details`` collects lines beginning
    ``CRASHED|TIMEOUT|SLOW|FAIL `, and ``decision.classify`` maps them to the
    flow's states.  A new word here would be a state nobody classifies.
    """
    lines: list[str] = []
    for r in results:
        if r.verdict == 'crashed':
            lines.append(f'CRASHED {r.id} {r.detail or f"({r.seconds:.1f}s)"}')
        elif r.verdict == 'timeout':
            lines.append(f'TIMEOUT {r.id} {r.detail or f"({r.seconds:.1f}s)"}')
        elif r.verdict == 'slow':
            lines.append(f'SLOW {r.id} {r.detail or f"({r.seconds:.1f}s)"}')
        elif r.verdict == 'diff':
            lines.append(f'FAIL {r.id} {r.detail} ({r.seconds:.1f}s)')
            for lineno, got, want in (r.diffs or [])[:50]:
                lines.append(f'    line {lineno}: {got!r} != {want!r}')
        else:
            lines.append(f'PASS {r.id} ({r.seconds:.1f}s)')
    total = counts.total
    lines.append(f'PASS: {counts.p}, FAIL: {total - counts.p}, TOTAL: {total}')
    lines.append(f'Benchmark Total CPU time: {cpu_total:.3f}s')
    return '\n'.join(lines) + '\n'


# ── the origin ────────────────────────────────────────────────

def origin_ready(plan, env: Env) -> bool:
    """Whether the origin can judge a run — COMPLETELY, not approximately.

    For a golden-file corpus: every case the plan declares must have its
    golden present.  The old check was ``any(rglob('*.out'))``, so a killed
    reference run leaving one file of 132 reported ready, and the cases
    without a golden were then scored as runtime-only passes.

    For a self-verifying corpus there is nothing to store, so the plan names
    a stamp recording that a clean reference run happened.
    """
    if plan.golden.source == 'none':
        return bool(plan.golden.stamp) and \
            (Path(resolve(plan.golden.root, env)) / plan.golden.stamp).is_file()
    for case in discover(plan, env):
        case = _with_golden(plan, env, case)
        if case.golden is None or not case.golden.is_file():
            return False
    return True


def _with_golden(plan, env: Env, case: Case) -> Case:
    if case.golden is not None or not plan.golden.path:
        return case
    bind = _bindings(env, case)
    case.golden = Path(resolve(plan.golden.root, env)) / \
        _spell(plan.golden.path, bind)
    return case


def ensure_reference(plan, env: Env) -> int:
    """Run the REFERENCE instance through this plan and keep the goldens.

    Returns how many were captured; ``0`` means nothing usable, and the
    caller must not proceed.  A failed run captures nothing — that is the
    rule, in one place, instead of a guard each corpus's hook had to
    remember.
    """
    ref = Env(target=env.target, tree=env.tree, origin=env.origin,
              results=env.results, jobs=env.jobs, cases_root=env.cases_root,
              protocol_tests=env.protocol_tests)
    # The reference run must not be judged against goldens that do not exist
    # yet, so it runs with the comparison assertions removed and the plan's
    # own requirement relaxed to "the cases ran".
    probe = _without_golden_assertions(plan)
    suite = run(probe, ref)
    if not suite.ok:
        return 0

    if plan.golden.source == 'none':
        stamp = Path(resolve(plan.golden.root, env)) / plan.golden.stamp
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text('a clean reference run reported this origin usable\n',
                         encoding='utf-8')
        return 1

    n = 0
    # The cases the run actually EXECUTED, not a fresh discovery.  `produced`
    # is bound when a case runs, and a restart chain binds it to the chain's
    # own work dir (`work/chain_<id>/`), which is where the artifact lands.
    # Re-discovering here returned bare cases whose `produced` was empty, so
    # every such case was skipped and reported as "no golden" -- measured:
    # 19 of 132, exactly the chain members.
    for case in suite.cases.values():
        case = _with_golden(plan, env, case)
        src = case.produced.get('out')
        if src is None or case.golden is None or not Path(src).is_file():
            continue
        case.golden.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(case.golden))
        n += 1
    return n


def _without_golden_assertions(plan):
    """*plan* with the golden comparison dropped, for the reference run.

    Only the assertions that need a golden are removed; everything else — the
    accepted exit codes, the artifact check, the case budget — still applies,
    so a reference run that crashes or times out captures nothing.
    """
    import dataclasses
    kept = tuple(a for a in plan.assertions
                 if a.get('kind') not in ('text_lockstep', 'field_assertion'))
    return dataclasses.replace(
        plan, assertions=kept,
        summary={'require_zero': tuple(plan.summary.get('require_zero', ())),
                 'require_min': {'total': 1}})
