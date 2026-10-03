"""A corpus's ACCEPTANCE, as data — the verification half of the protocol.

The build half of this repo already answers "how is this project built?" from
a committed protocol, and every consumer reads that data instead of asking a
per-corpus Python module.  This is the same move for the other question the
flow asks a corpus: **how do we know a run of it was correct?**

Three corpora, three genuinely different answers, and today all three are
700~800 lines of Python plus four callables the harness imports out of them::

    NASTRAN-95   an executable, judged by a filtered line-lockstep diff
                 against goldens, with 19 restart chains that hand a ``.sof1``
                 forward, and a per-run ``nastran.nml`` preparation
    MYSTRAN      an executable, judged by the corpus's OWN per-quantity
                 criteria (``cases.txt`` columns 3-6, schema in its
                 ``case_definition.py``) — which nothing in this repo reads
    LAPACK       a built TREE, not an executable, judged by its drivers'
                 own self-check tables — no golden file at all

The vocabulary below is exactly as large as those three need and no larger.
Every section is optional except the ones whose absence would let a run pass
without anyone being able to see that it did anything (see
:meth:`VerificationPlan.problems`).

WHY A SEPARATE FILE, not a field of the private protocol
--------------------------------------------------------
The protocol's awkwardest correctness bug was ``merge_variants`` dropping a
fact-carrying field: the protocol carries a fortran half and a cpp half, so
merging them is a real operation that must pass every fact through, and
``own_roots`` and ``tests`` were both silently left behind once.  An
acceptance plan has **no variant dimension** — the same plan judges a
pure-Fortran run and a mixed run — so a file that has no variants cannot
repeat that class of bug at all.

It travels like the protocol does: committed gzipped, expanded on use.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .private_protocol import TREE, check_tree_invariant

#: The only plan format this consumer reads.  A file from another version is
#: refused rather than coerced: this is the contract between the onboarding
#: skill and the flow, and guessing at a newer one produces a run judged by
#: rules nobody wrote.
PLAN_VERSION = 1

#: Bindings a plan may spell.  ``${TREE}`` is the CAPTURE tree (where the
#: reference build lives); these are the RUN's roots, and they are different
#: things — conflating them is the failure this repo keeps re-learning, where
#: a path exists but not where the plan looks for it.
BINDINGS = ('target', 'tree', 'origin', 'results', 'work', 'jobs',
            'input.name', 'input.stem', 'case_id', 'scratch')

#: A golden's provenance.  Spelled in the data because it decides what the
#: verdict MEANS: a golden captured by running the reference build proves the
#: two agree with each other, which is stability, not correctness.
GOLDEN_SOURCES = ('reference_run', 'none')

#: What a plan may require to have happened, beyond "nothing failed".  These
#: are the corpus-free verdict kinds; a corpus combines them.
VERDICT_KINDS = ('diff', 'crash', 'timeout', 'slow')


class PlanError(ValueError):
    """A file that cannot be read as a verification plan."""


def _require(d: dict, key: str, where: str) -> Any:
    if key not in d:
        raise PlanError(f'{where} has no {key!r}')
    return d[key]


def _spell_ok(text: str) -> str | None:
    """``None`` when *text* is spellable, else why it is not."""
    return check_tree_invariant(text)


@dataclass(frozen=True)
class Target:
    """What the run is performed ON.

    ``product`` — one of the protocol's own products, named as the protocol
    names it (NASTRAN's ``nastran``, MYSTRAN's ``mystran``).  ``tree`` — a
    built tree whose members are not one artifact: LAPACK is a library with
    214 driver executables, and the harness's habit of handing it
    ``<tree>/bin/lapack`` produced a path that does not exist, surviving only
    because one corpus's Python hook threw it away.
    """

    kind: str                     # 'product' | 'tree'
    product: str = ''
    #: Where the thing under test may be, as BINDINGS, in search order.
    #:
    #: A product's directory is not one fixed place: a project's own build
    #: puts it where its build system does, and a generated flat build puts
    #: it under `<tree>/bin` — the SAME plan has to run against both (§B
    #: self-verify runs it on the reference, then on the flat build).  So the
    #: plan declares the CANDIDATES and the mechanism resolves by NAME, taking
    #: the one that is there.  More than one hit is refused rather than picked
    #: silently — same rule as everywhere else in this tool.
    dirs: tuple[str, ...] = ()

    def to_json(self) -> dict:
        out: dict = {'kind': self.kind}
        if self.product:
            out['product'] = self.product
        if self.dirs:
            out['dirs'] = list(self.dirs)
        return out

    @classmethod
    def from_json(cls, d: dict) -> 'Target':
        return cls(kind=d.get('kind', ''), product=d.get('product', ''),
                   dirs=tuple(d.get('dirs') or ()))


@dataclass(frozen=True)
class Run:
    """How one case (or the whole suite) is executed.

    ``process`` — the corpus's own program, once per case, with the accepted
    exit codes spelled out (NASTRAN accepts 0 **and** 2; a signal is never
    acceptable and is derived from a negative returncode, not declared).
    ``suite`` — the build system's own runner over a registered set
    (``ctest -j {jobs} -V --timeout {case_timeout_s}``): the corpus is a built
    tree and its cases are the tests its build registered.

    ``ctest``/``-V``/``-j`` are a tool's spelling, not a corpus name — the
    same licence ``flat_cmake`` takes when it emits ``add_test``.

    ``case_timeout_s`` is the PER-CASE bound.  It is deliberately its own
    field: the harness used to hand the script the whole SUITE's process
    bound in its place, which measured ~30min against a legitimate 277s.
    """

    kind: str                     # 'process' | 'suite'
    argv: tuple[str, ...] = ()
    cwd: str = ''
    jobs: int = 0
    exit_accept: tuple[int, ...] = ()
    case_timeout_s: int = 0

    def to_json(self) -> dict:
        out: dict = {'kind': self.kind}
        if self.argv:
            out['argv'] = list(self.argv)
        if self.cwd:
            out['cwd'] = self.cwd
        if self.kind == 'suite':
            out['jobs'] = self.jobs
        out['exit_accept'] = list(self.exit_accept)
        out['case_timeout_s'] = self.case_timeout_s
        return out

    @classmethod
    def from_json(cls, d: dict) -> 'Run':
        return cls(kind=d.get('kind', ''),
                   argv=tuple(d.get('argv', ())),
                   cwd=d.get('cwd', ''),
                   jobs=int(d.get('jobs', 0) or 0),
                   exit_accept=tuple(d.get('exit_accept', ())),
                   case_timeout_s=int(d.get('case_timeout_s', 0) or 0))


@dataclass(frozen=True)
class Golden:
    """What the produced artifacts are compared against, and where it lives.

    ``reference_run`` — the corpus has no shipped truth, so the reference
    build is run through THIS SAME plan once and what it produces becomes the
    golden.  That is one rule for all corpora, and it is what makes the
    reference and the candidate traverse the same code path.

    ``none`` — a self-verifying corpus (LAPACK): its drivers compute their own
    residuals, so there is nothing to store.  The origin is then a STAMP
    recording that a clean reference run happened, and ``Stamp`` names it —
    an empty ``source: none`` with no stamp would mean every origin is ready,
    including a partial one.
    """

    source: str                   # 'reference_run' | 'none'
    root: str = ''                # a BINDING, e.g. '{origin}' or '${TREE}'
    path: str = ''
    stamp: str = ''

    def to_json(self) -> dict:
        out: dict = {'source': self.source, 'root': self.root, 'path': self.path}
        if self.stamp:
            out['stamp'] = self.stamp
        return out

    @classmethod
    def from_json(cls, d: dict) -> 'Golden':
        return cls(source=d.get('source', ''), root=d.get('root', ''),
                   path=d.get('path', ''), stamp=d.get('stamp', ''))


@dataclass
class VerificationPlan:
    """One corpus's acceptance, whole.

    ``cases`` is the provenance of the case list and is deliberately open: the
    three corpora state it three ways — a directory scan, an ``INCLUDE``-
    chained case list, and the build system's own registry — and a fourth
    shape should be declarable without touching this module.
    """

    plan_version: int = PLAN_VERSION
    target: Target = field(default_factory=lambda: Target(kind=''))
    #: The build protocol this plan belongs to, as a name relative to the
    #: plan (or a binding).  Declared rather than assumed: the old harness
    #: hardcoded one project-named file, and a plan that has to be found by
    #: guessing a name is a plan the mechanism is coupled to.  Empty means
    #: the caller names it (the CLI's `--protocol`).
    protocol: str = ''
    cases: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    staging: tuple[dict, ...] = ()
    run: Run = field(default_factory=lambda: Run(kind=''))
    produced: tuple[dict, ...] = ()
    golden: Golden = field(default_factory=lambda: Golden(source=''))
    assertions: tuple[dict, ...] = ()
    #: Restart chains: ``order[case_id]`` lists the cases that must run
    #: BEFORE it, in one shared work dir, with state carried forward.
    #: Declared because only the corpus knows which of its cases depend on
    #: another's intermediate state — NASTRAN's 19 chains are why exactly
    #: those 19 failed (of 132) while everything else passed.
    chains: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    provenance: str = ''
    comment: str = ''

    # ── serde ─────────────────────────────────────────────────

    def to_json(self) -> dict:
        # Normalized through JSON rather than trusted: the free-form sections
        # (cases/inputs/report/summary/...) are written by the onboarding
        # skill, and a tuple that survives here but comes back a list makes
        # `to_json()` differ from reading the file it wrote — so a plan
        # compared against a freshly loaded one would look changed when it is
        # not.  Normalizing once makes that hold by construction.
        return json.loads(json.dumps({
            'plan_version': self.plan_version,
            'target': self.target.to_json(),
            'protocol': self.protocol,
            'cases': dict(self.cases),
            'inputs': dict(self.inputs),
            'staging': [dict(s) for s in self.staging],
            'run': self.run.to_json(),
            'produced': [dict(p) for p in self.produced],
            'golden': self.golden.to_json(),
            'assertions': [dict(a) for a in self.assertions],
            'chains': dict(self.chains),
            'report': dict(self.report),
            'summary': dict(self.summary),
            'provenance': self.provenance,
            'comment': self.comment,
        }))

    @classmethod
    def from_json(cls, d: dict) -> 'VerificationPlan':
        if not isinstance(d, dict):
            raise PlanError(f'a plan must be an object, got {type(d).__name__}')
        got = d.get('plan_version')
        if got != PLAN_VERSION:
            raise PlanError(
                f'plan_version {got!r} is not {PLAN_VERSION}: this consumer '
                f'reads one version of the format and refuses to guess at '
                f'another, because the guess is a run judged by rules '
                f'nobody wrote')
        return cls(
            plan_version=PLAN_VERSION,
            target=Target.from_json(_require(d, 'target', 'plan')),
            protocol=d.get('protocol', ''),
            cases=dict(_require(d, 'cases', 'plan')),
            inputs=dict(d.get('inputs', {})),
            staging=tuple(dict(s) for s in d.get('staging', ())),
            run=Run.from_json(_require(d, 'run', 'plan')),
            produced=tuple(dict(p) for p in d.get('produced', ())),
            golden=Golden.from_json(_require(d, 'golden', 'plan')),
            assertions=tuple(dict(a) for a in d.get('assertions', ())),
            chains=dict(d.get('chains', {})),
            report=dict(d.get('report', {})),
            summary=dict(_require(d, 'summary', 'plan')),
            provenance=d.get('provenance', ''),
            comment=d.get('comment', ''),
        )

    # ── disk ──────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=2) + '\n',
                              encoding='utf-8')

    @classmethod
    def load(cls, path: str | Path) -> 'VerificationPlan':
        """Read the plan, expanding its committed gzipped form if need be.

        Same treatment the protocol gets, for the same reason: the plan is
        large enough that a flat JSON would be noise in every diff, and a
        checkout that has not expanded it yet must still work.
        """
        src = Path(path)
        if not src.is_file():
            packed = Path(str(src) + '.gz')
            if not packed.is_file():
                raise PlanError(
                    f'the verification plan {src} is not there, and neither '
                    f'is its committed form {packed}')
            src.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(packed, 'rt', encoding='utf-8') as fh:
                plan = cls.from_json(json.load(fh))
            plan.save(src)
            return plan
        if src.suffix == '.gz':
            with gzip.open(src, 'rt', encoding='utf-8') as fh:
                return cls.from_json(json.load(fh))
        return cls.from_json(json.loads(src.read_text(encoding='utf-8')))

    # ── the completeness authority ────────────────────────────

    def problems(self) -> list[str]:
        """Everything wrong with this plan as written; ``[]`` is valid.

        A list rather than a raise, like the protocol's: whoever is holding a
        run that did not behave wants every gap at once.

        The first check is the one that matters most.  A plan that does not
        say what must HAVE HAPPENED can be satisfied by a run whose results
        were never read — which is not a hypothetical: two of the three
        corpora's summaries currently parse to zero passing cases, and their
        zero failures, zero crashes and zero timeouts made them read clean.
        """
        bad: list[str] = []

        bad.extend(self._chain_problems())
        bad.extend(self._run_problems())

        req_min = self.summary.get('require_min')
        if not isinstance(req_min, dict) or not req_min:
            bad.append(
                'summary.require_min is missing or empty: a verdict with no '
                'checks behind it is not a pass, so a plan must name what it '
                'requires to have happened (e.g. {"pass": 1})')
        else:
            for k, v in req_min.items():
                if not isinstance(v, int) or v < 1:
                    bad.append(
                        f'summary.require_min[{k!r}] is {v!r}: a requirement '
                        f'of zero requires nothing, which reads as a '
                        f'declaration and is not one')

        for k in self.summary.get('require_zero', ()) or ():
            if k not in VERDICT_KINDS:
                bad.append(
                    f'summary.require_zero names {k!r}, which is not one of '
                    f'{VERDICT_KINDS} — a plan cannot require the absence of '
                    f'something the mechanism never observes')

        if self.target.kind not in ('product', 'tree'):
            bad.append(
                f'target.kind is {self.target.kind!r}, not "product" or '
                f'"tree" — the two shapes a corpus 主体 has')
        elif self.target.kind == 'product' and not self.target.product:
            bad.append(
                'target.kind is "product" but target.product is empty: the '
                'plan has to name which of the protocol\'s products is the '
                'thing under test')
        for i, d in enumerate(self.target.dirs):
            why = self._binding_problem(d, f'target.dirs[{i}]')
            if why:
                bad.append(why)

        if self.run.kind not in ('process', 'suite'):
            bad.append(f'run.kind is {self.run.kind!r}, not "process" or '
                       f'"suite"')
        elif self.run.kind == 'process':
            if not self.run.argv:
                bad.append('run.argv is empty: a process run with no command '
                           'line runs nothing')
            if not self.run.exit_accept:
                bad.append(
                    'run.exit_accept is empty: a corpus that accepts no exit '
                    'code fails every case, and one that accepted "any" would '
                    'be declaring that signals are fine')
        if not self.run.case_timeout_s:
            bad.append('run.case_timeout_s is 0: an unbounded case is a hang '
                       'waiting to happen')

        # A per-case run is handed a bare input NAME while its cwd is the
        # work dir, so a plan that does not say what to put there describes a
        # run that reads nothing.  Measured on this mechanism's first
        # version: with staging ignored, every fixture case passed — the
        # workload fell through to a default branch and reported clean.
        # A `suite` run has no per-case input: its registrations carry their
        # own commands.
        if self.run.kind == 'process':
            frm = (self.inputs or {}).get('from')
            if frm not in ('input_only', 'case_dir'):
                bad.append(
                    f'inputs.from is {frm!r}, not "input_only" or "case_dir": '
                    f'a per-case run is given a bare input name, so a plan '
                    f'that does not say what to stage describes a run that '
                    f'reads nothing — and one that then falls through to a '
                    f'default branch reports a clean pass')

        if self.golden.source not in GOLDEN_SOURCES:
            bad.append(
                f'golden.source is {self.golden.source!r}, not one of '
                f'{GOLDEN_SOURCES}')
        elif self.golden.source == 'none':
            if not self.golden.stamp:
                bad.append(
                    'golden.source is "none" with no golden.stamp: a '
                    'self-verifying corpus still has to say what makes its '
                    'origin usable, and without that every origin counts as '
                    'ready — including one a killed reference run left half '
                    'written')
        else:
            why = self._binding_problem(self.golden.root, 'golden.root')
            if why:
                bad.append(why)
            else:
                why = self._reference_run_under_a_run_root(self.golden.root)
                if why:
                    bad.append(why)

        for i, p in enumerate(self.produced):
            name = p.get('name', '')
            if not name:
                bad.append(f'produced[{i}] has no name: nothing to look for')
                continue
            why = _spell_ok(name)
            if why:
                bad.append(f'produced[{i}].name: {why}')

        for i, s in enumerate(self.staging):
            if not s.get('op'):
                bad.append(f'staging[{i}] has no op: an operation this '
                           f'mechanism cannot perform cannot be declared')

        for i, a in enumerate(self.assertions):
            if not a.get('kind'):
                bad.append(f'assertions[{i}] has no kind: the verdict it '
                           f'would render is not one the mechanism knows')

        for where, why in self._spelling_problems():
            bad.append(f'{where}: {why}')

        return bad

    def _run_problems(self) -> list[str]:
        """What is wrong with how the plan says its cases are executed.

        One rule, learned by measuring it: a ``suite`` run's WIDTH is part of
        its measurement, not a harness default.  Its drivers are CPU-bound and
        contend, so the same `src/`-independent corpus measured 218.5s at one
        width and 152.2s at another — a 1.4x spread that is pure measurement
        artifact and would read as a performance regression.

        A plan that binds ``{jobs}`` must therefore say what it is.  It used to
        bind the BUILD policy instead ("every logical core"): the field below
        existed, was serialized, and was read by nothing, so the declared width
        was silently discarded in favour of whatever the machine had.
        """
        bad: list[str] = []
        if self.run.kind == 'suite' and '{jobs}' in ' '.join(self.run.argv):
            if self.run.jobs < 1:
                bad.append(
                    'run.jobs is missing or zero but run.argv binds {jobs}: a '
                    'suite\'s width is part of its measurement (its drivers '
                    'contend), so binding it without declaring it measures '
                    'whatever the machine happens to have')
        return bad

    def _chain_problems(self) -> list[str]:
        """What is wrong with the declared restart chains.

        A chain is ORDER plus HANDOVER, and only the corpus knows either —
        NASTRAN's 19 chains are exactly why 19 of its 132 cases failed while
        everything else passed.  Both halves must be declared: a shared work
        directory without the handover rule reproduces the same 19 failures,
        because the subsystem looks for the state under the CURRENT case's
        own name.
        """
        bad: list[str] = []
        order = self.chains.get('order')
        if order is None:
            if self.chains:
                bad.append(f'chains has {sorted(self.chains)} but no "order" — a '
                           'chain that does not say what precedes what is not a '
                           'chain')
            return bad
        if not isinstance(order, dict):
            return [f'chains.order is {type(order).__name__}, not a mapping of '
                    'case to its prerequisites']
        for case_id, deps in order.items():
            if not isinstance(deps, (list, tuple)):
                bad.append(f'chains.order[{case_id!r}] is not a list — one case '
                           'to one ordered list of prerequisites')
                continue
            if case_id in deps:
                bad.append(f'chains.order[{case_id!r}] lists itself as its own '
                           'prerequisite')
            seen = set()
            for dep in deps:
                if dep in seen:
                    bad.append(f'chains.order[{case_id!r}] names {dep!r} twice '
                               '— a repeated prerequisite is a declaration that '
                               'disagrees with itself')
                seen.add(dep)
        carry = self.chains.get('carry') or {}
        named = carry.get('as')
        if not named:
            if order:
                bad.append('chains declares prerequisites but no "carry": the '
                           'prerequisites would share a work directory and hand '
                           'nothing forward — which is the failure the chains '
                           'exist to fix, since the state must be re-presented '
                           'under the NEXT case\'s own name')
        elif named not in {a.get('as') for a in self.produced}:
            bad.append(f'chains.carry.as is {named!r}, which is not one of this '
                       f"plan's produced artifacts "
                       f'{[a.get("as") for a in self.produced]} — the carried '
                       'file is an artifact the plan declares, and a carry rule '
                       'naming one that does not exist hands forward nothing')
        return bad

    def _binding_problem(self, text: str, where: str) -> str | None:
        """A root must be a BINDING, never a literal path.

        A literal root is not merely unportable: it reads as if it were
        portable, and points at whichever tree happened to sit beside the
        capture.
        """
        if not text:
            return f'{where} is empty: it names no root to look under'
        if text.startswith('{') or text.startswith(TREE):
            return None
        return (f'{where} is {text!r}, a literal path: a root names a BINDING '
                f'({", ".join("{" + b + "}" for b in BINDINGS[:4])}, or '
                f'{TREE!r} for the capture tree)')

    #: Where a golden captured by a reference run may live.  Both are OUTPUT
    #: roots that only this tool writes: `origin` is where `ensure_reference`
    #: copies what the run produced, and `results` is where a run reports.
    _RUN_ROOTS = frozenset({'origin', 'results'})

    def _reference_run_under_a_run_root(self, root: str) -> str | None:
        """A reference-run golden may only come from a RUN, never the repo.

        ``golden.source: reference_run`` means "this golden is what the
        project's own build produced when it ran".  A ``root`` naming the
        capture tree (``${TREE}``) instead makes it something else entirely:
        a file the PROJECT SHIPPED, read out of its repository.  That is a
        different claim with the same field — it says the tool's run agrees
        with a 1995 output from another machine, which is a comparison
        against another machine's rounding, its platforms, and its bugs.

        So the two are kept apart structurally.  A project's own reference
        output is INPUT: if a corpus ships one and it is worth comparing
        against, the comparison belongs in the project's declaration, not
        smuggled in as a golden.  The golden is always re-made here, by
        running the build.
        """
        if root == TREE or root.startswith(f'{TREE}/'):
            return (
                f'golden.root is {root!r} — the CAPTURE TREE — while '
                f'golden.source is "reference_run": a golden must be what a '
                f'run of the project PRODUCED, and a root inside the project '
                f'repository names a file the project SHIPPED.  Comparing '
                f'against the repository\'s own output proves agreement with '
                f'another machine, not that this build ran correctly.  Point '
                f'golden.root at a run output root ({", ".join("{" + b + "}" for b in sorted(self._RUN_ROOTS))}).')
        name = root.strip('{}') if root.startswith('{') else None
        if name is not None and name not in self._RUN_ROOTS:
            return (
                f'golden.root is {root!r} while golden.source is '
                f'"reference_run": only a run output root may hold a captured '
                f'golden ({", ".join("{" + b + "}" for b in sorted(self._RUN_ROOTS))}).  A golden read from the project '
                f'itself is the project\'s own reference output — an INPUT, '
                f'not a golden.')
        return None

    def _spelling_problems(self) -> list[tuple[str, str]]:
        """Every ``$`` in the plan, checked by the protocol's own rule."""
        out: list[tuple[str, str]] = []
        for where, text in self._spelled_strings():
            why = _spell_ok(text)
            if why:
                out.append((where, why))
        return out

    #: Keys whose value is a REGEX, not a path.  The ``$`` rule exists
    #: because a path with a bare ``$`` reaches a shell or a build system,
    #: which spells its own variables the same way and expands it to nothing.
    #: A pattern reaches ``re``, where ``$`` is the end anchor — applying the
    #: rule to it rejects a perfectly good pattern, which is what a
    #: self-verifying corpus's section heading is.
    _REGEX_KEYS = frozenset({'pattern', 'section_pattern'})

    def _spelled_strings(self) -> 'Iterator[tuple[str, str]]':
        yield 'run.cwd', self.run.cwd
        for i, tok in enumerate(self.run.argv):
            yield f'run.argv[{i}]', tok
        yield 'golden.path', self.golden.path
        for i, s in enumerate(self.staging):
            for k, v in sorted(s.items()):
                if isinstance(v, str) and k not in self._REGEX_KEYS:
                    yield f'staging[{i}].{k}', v
        for i, a in enumerate(self.assertions):
            for k, v in sorted(a.items()):
                if k in self._REGEX_KEYS:
                    continue
                if isinstance(v, str):
                    yield f'assertions[{i}].{k}', v
                elif isinstance(v, dict):
                    for kk, vv in sorted(v.items()):
                        if isinstance(vv, str) and kk not in self._REGEX_KEYS:
                            yield f'assertions[{i}].{k}.{kk}', vv
