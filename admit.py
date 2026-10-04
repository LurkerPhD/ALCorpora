#!/usr/bin/env python3
"""Admit one project into `benchmark/` — the single entry point.

This is the DRIVER. It owns the flow: parse the arguments, drive the steps in
order, and write the row. The mechanical half it drives — numbering, fetching,
running the native toolchain, §A, §B, recording — lives in
`skills/fetch-benchmark-project/scripts/fetch.py`, which this module reaches
through the namespace handed to `main`. Nothing is duplicated between them.

Why the flow is its own module rather than more of the skill: it used to live
in 189 lines of prose. Retries, ordering, and when to stop were described in
a document no test could hold, and the code it called was one mechanical
segment of that description. A driver that is a program is a driver that can
be tested, and the seams where a project needs something only judgement can
supply become TYPED CONTRACTS instead of instructions in a paragraph.

The order is the definition of "legal", and it is not negotiable:

  1. it builds natively with its own toolchain;
  2. it verifies natively (its own tests pass);
  3. §A — a build generated from the protocol alone replays the project;
  4. §B — the plan is green on the project's build AND on the replay.

A failure at 1-2 REFUSES a first admission (not a legal project yet). A
failure at 3-4 is REPORTED with the recorded protocols still written, because
that failure is the next thing to develop — the point of a benchmark.

Exit status: 0 legal, 1 not legal, 2 usage.

Stage 0 is a pure move: the flow runs the same steps, in the same order, with
the same messages as before, and every seam is closed. Replaying the corpus
through it must reproduce `benchmark.csv` exactly, which is what makes the
move safe to make before any agent is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

#: This file sits at the repository root, so the repo IS its directory.
REPO = Path(__file__).resolve().parent

#: How many times seam A may propose a fix before the flow stops asking.
#: Bounded so a seam cannot spin, and small because a fix that has not
#: worked after a few readings is a diagnosis to hand back, not a guess to
#: keep making.
MAX_SEAM_PASSES = 3

#: The mechanical half. It is not importable as a package (its directory
#: carries a hyphen), so it is loaded by path.
FETCH_SCRIPT = REPO / 'skills/fetch-benchmark-project/scripts/fetch.py'


def fetch_namespace() -> dict:
    """Load the mechanical half and return its module namespace.

    A plain dict rather than the module object, because that is what a caller
    inside the module can hand over with `globals()` — and a caller that hands
    over its OWN namespace is what lets a test patch `native` or `BENCH_CSV`
    on the skill module and have the driver observe it.
    """
    spec = importlib.util.spec_from_file_location('fetch_skill', FETCH_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {FETCH_SCRIPT}')
    module = importlib.util.module_from_spec(spec)
    # Registered before execution so the module can be resolved by name from
    # inside itself, and so a second load resolves to this one.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.__dict__


def _make_agent(args):
    """The seam agent.  A seam that cannot reach it falls back to no agent.

    There is no switch to close the seams: an unreachable agent IS the closed
    state (`AgentUnavailable` makes the seam give up, and the step fails
    exactly as it would with no agent at all).  That keeps one path instead of
    two, and it keeps a missing `claude` from ever reading as a verdict about
    a project — it reads as `AGENT_ERROR`, which is what it is.
    """
    from protocolize.agent import Agent
    cmd = (args.agent_cmd
           or os.environ.get('PROTOCOLIZE_AGENT_CMD')
           or 'claude')
    return Agent(cmd=cmd, max_calls=args.max_agent_calls)


#: What has actually been seen at a native build failure, kept HERE because it
#: is the seam's own knowledge.
#:
#: It used to live in 30 lines of the skill's prose, which an agent read
#: before deciding what to try.  The flow asks directly now, so the instances
#: move to the place that does the asking — they are the seam's contract
#: material, not a document about it.  Each is a SHAPE of failure, not a
#: recipe: what was wrong, and what kind of fix answered it.
_NATIVE_DIAGNOSIS = """\
Failures of this shape have been seen before, and each named its own cause:
  * a Makefile spelling target paths with BACKSLASHES (written on Windows):
    the slashed path names a real file, the backslashed one does not.
  * a Fortran dialect this compiler no longer accepts by default
    (argument-mismatch, BOZ constants, Cray pointers): the compiler documents
    a flag to allow it.
  * a missing tool the build calls.
Read the actual output above rather than matching these; they are shapes, not
a list."""

#: The same, for the step where the plan is captured and found unusable.
_DECLARATION_DIAGNOSIS = """\
This step means the captured plan needs something the tree cannot show. Two
shapes have been seen:
  * a program that needs a home directory, a resource tree or a program config
    before it will run — without them it exits 0 and writes NOTHING, which
    reads as a broken project rather than a missing declaration;
  * a comparison whose volatile lines and numeric slack are the project's own
    (a timestamp, an elapsed time, a residual whose sign is meaningless).

WHICH LINES ARE VOLATILE — read this carefully, it is where a plausible answer
goes wrong. Prefer, in this order:
  1. the project's OWN statement: its comparison script, its test harness, its
     documented tolerances;
  2. running the same binary several times and diffing.
A two-run diff is a LOWER BOUND, not an answer: it finds only what happened to
vary in THOSE two runs. Measured on a corpus whose own notes record that three
related counters vary between runs of the same binary — a re-derivation that
diffed two runs caught two of the three, passed 132/132 that day, and would
have failed on another. If several related lines come from one mechanism
(a memory-pool report, a statistics block, a timer), treat the mechanism as
volatile, not just the line you saw move.
"""


def _tree_census(src: Path, limit: int = 400) -> str:
    """A shallow listing of the tree, handed to the seam up front.

    Measured, and it is where the money went: a seam agent's answer cost
    $13-31 and 3.5-6.7 MILLION input tokens, 98% of them cache reads — it was
    re-reading the whole conversation on every retry, and it was retrying
    because every way it tried to LOOK at the tree was refused. Thirteen to
    fifteen denials per seam.

    The agent is supposed to read the tree; it just must not BUILD or RUN the
    project. Refusing the reading was the wrong lever — it turned a fact the
    agent needs into a fight, and the fight is what cost the tokens. So the
    flow looks once and hands the answer over.
    """
    lines: list[str] = []
    root_files = []
    try:
        entries = sorted(src.iterdir(), key=lambda p: (p.is_file(),
                                                        p.name.lower()))
    except OSError as exc:
        return f'(the tree could not be listed: {exc})'
    subdirs = [e for e in entries if e.is_dir() and e.name != '.git']
    root_files = [e.name for e in entries if e.is_file()]
    if root_files:
        lines.append('  (files) ' + ' '.join(root_files[:40]))
    for d in subdirs[:20]:
        try:
            kids = sorted(d.iterdir(), key=lambda p: (p.is_file(),
                                                      p.name.lower()))
        except OSError:
            continue
        names = [k.name + ('/' if k.is_dir() else '') for k in kids]
        lines.append(f'  {d.name}/: ' + ' '.join(names[:40]))
        if len(lines) >= limit // 8:
            break
    return '\n'.join(lines) if lines else '  (empty)'


def _seam_prompt(seam: str, tree: Path, situation: str, want: str,
                 census: str = '') -> str:
    """What a seam hands the agent: the situation, and the contract.

    The contract is generated from `seams.KINDS`, so the schema the agent is
    told and the schema the flow enforces cannot drift apart — a prompt that
    described a shape the validator refuses would make every answer look
    wrong.

    The tree is named rather than pasted.  The agent runs with its cwd there
    and can read what it needs; a prompt carrying a whole tree would be a
    copy that is stale the moment the project changes.
    """
    seen = {'A': _NATIVE_DIAGNOSIS,
            'D': _DECLARATION_DIAGNOSIS,
            'E': _DECLARATION_DIAGNOSIS}.get(seam, '')
    seen = f'\n{seen}\n' if seen else ''
    return f"""You are answering one question for an automated flow that is
admitting a project into a benchmark corpus. The flow is a program; you are
the part that needs judgement, and you answer in JSON.

THE PROJECT TREE: {tree}
Your working directory is that tree. What is in it, listed for you so that
you do not have to ask:

{census or '(not listed)'}

That listing is the tree and its immediate children only; read any file you
need in full, and search deeper as you like. Reading is expected — do plenty
of it. Do not guess from names.

DO NOT BUILD OR RUN THE PROJECT. Do not invoke its build system, its
compiler, or its programs. You have no need to: the flow RUNS whatever you
propose and judges it by the result, so a command you never executed is still
a command it will test. An answer that depends on your having run something is
an answer the flow cannot use — and it will refuse it rather than guess.

WHAT HAPPENED:
{situation}
{seen}
WHAT THE FLOW NEEDS FROM YOU:
{want}

ANSWER WITH ONE JSON OBJECT, on its own, with a `kind` field:

* "kind": "native_commands" — how the project builds and verifies itself.
  Fields: "configure", "build", "test" (each a shell line, or a list of
  shell lines run in order; omit "configure" when there is nothing to
  configure), and "tree_subdir".

  "tree_subdir" is where the BUILD SYSTEM'S OWN FILES live, relative to the
  tree — the files the flow reads to learn what the build would run (a
  Makefile, a build.ninja). It is NOT where the commands are run from. Get it
  wrong and the flow reports that the tree "does not answer what would you
  run". Worked examples:
    * a build of `make -C source/mds` and `make -C source/mis`  ->  "source"
    * a CMake build into `build/` beside the sources            ->  "build"
    * a Makefile at the checkout root                           ->  omit it
  These are the project's OWN commands, not ones you invented.

* "kind": "prepare_ops" — patches the project needs before it builds or
  verifies at all. Fields: "ops", a list of
  {{"op": <name>, "check": <shell line exiting 0 when ALREADY applied>,
   "run": <shell line that applies it>}}.
  A patch without a "check" is refused: the flow cannot tell an applied
  patch from an unapplied one, so the op is neither idempotent nor
  reproducible. Note that a build script is part of the project's source, so
  editing one IS a patch and belongs here rather than in native_commands.

* "kind": "facts" — what the project's programs need in order to RUN, which
  no tree records. Field: "value", and its keys must be ones the capture
  READS — an object it cannot read is inert, and is refused:
    "preparation" : the environment and staging a program needs. Its keys are
       "home_env" (the variable naming a home directory),
       "config_env" (the variable naming a program config),
       "config_glob" (that config's file name),
       "resource_dir" (a resource tree relative to home),
       "scratch_subdir", "substitutions" ([[old, new]] applied to the config),
       "alias_upper" (stage an uppercase spelling of each file),
       "env" ({{name: value}} set for the run).
    "artifact"     : {{suffix, golden_suffix, golden_root, state_suffix}}.
    "field_model"  : a per-table column model (see the plan template).
    "chains"       : {{order, carry}} for cases that restart from a
                     previous one.
   Give the keys that apply; do not invent new ones — they are read by key.

* "kind": "declaration" — how two runs are legitimately COMPARED. Field:
  "value", with "comparison": {{"drop": [<regex for each volatile line>],
  "rtol": <number>, "atol": <number>}}. The capture reads exactly those three
  under "comparison" and nothing else. Read these from the project; do not
  infer them from the outputs, because a comparison that inferred them would
  call a timestamp difference a real one.

* "kind": "protocol_gap" — the form above cannot express what this project
  needs. Fields: "missing" (what cannot be expressed) and "evidence". Use
  this instead of forcing an answer into a field that does not fit.

Answer with the JSON object and nothing else.
"""


def _seam_native(M: dict, args, agent, src: Path, dest: Path,
                 log: str) -> bool:
    """Seam A: the project did not build or verify natively.

    Returns True when the agent's answer was ADOPTED — which means it was
    validated by running it, not by reading it. A `native_commands` becomes
    the commands the next `native()` call uses; a `prepare_ops` becomes
    patches that will be applied and then checked by that same build. Neither
    is adopted on the agent's say-so.
    """
    work = dest / '.work' / 'agent'
    agent.log_dir = work
    situation = ('The project did not build and verify with its own commands.\n'
                 f'The commands tried were:\n'
                 f'  configure: {args.configure}\n'
                 f'  build:     {args.build}\n'
                 f'  test:      {args.test}\n'
                 f'Output, tail:\n{log[-4000:]}')
    want = ('The commands that DO build and verify this project, or the '
            'patches it needs before it can.')
    from protocolize.agent import AgentUnavailable, SeamFailed
    try:
        resp = agent.ask(
            'A', _seam_prompt('A', src, situation, want,
                              _tree_census(src)), src,
            signature=hashlib.sha256(log.encode()).hexdigest()[:16])
    except (AgentUnavailable, SeamFailed) as exc:
        return _report_seam_failure(exc)

    from protocolize import seams
    if resp.kind == 'protocol_gap':
        # Not a failure and not retried: the FORM is inadequate, and growing
        # the form is not something a seam may do.
        print(f'PROTOCOL_GAP — {resp.payload.get("missing")}\n'
              f'  evidence: {resp.payload.get("evidence", "")}\n'
              f'  the protocol must grow for this; the flow stops here.',
              file=sys.stderr)
        return False
    if resp.kind == 'prepare_ops':
        args.prepare_ops = list(args.prepare_ops or []) + list(
            resp.payload['ops'])
        print(f'  seam A: {len(resp.payload["ops"])} patch(es) proposed; '
              f'the next build will decide whether they work')
        return True
    if resp.kind == 'native_commands':
        _apply_native_commands(args, resp.payload)
        print('  seam A: the build commands were replaced; re-running')
        return True
    print(f'  seam A answered {resp.kind!r}, which does not resolve a '
          f'build failure', file=sys.stderr)
    return False


def _apply_native_commands(args, payload: dict) -> None:
    """Wholesale: an omitted command is CLEARED, not left at its default.

    Measured on a hand-written make project. The contract says to omit
    `configure` when there is nothing to configure, and the agent did exactly
    that — but only keys present in the answer were overwritten, so the CMake
    default survived. The answer was adopted, the build failed on `cmake -S
    ...` again, and a make project could not build through the seam at all.

    A `native_commands` answer is a STATEMENT about how this project builds,
    so what it does not name is part of the statement.
    """
    for key in ('configure', 'build', 'test'):
        v = payload.get(key)
        if isinstance(v, list):
            v = ' && '.join(v)
        setattr(args, key, v if isinstance(v, str) else '')
    if isinstance(payload.get('tree_subdir'), str):
        args.tree_subdir = payload['tree_subdir']


def _tree_subdir_without_a_build_system(src: Path, subdir: str) -> str:
    """Why *subdir* is not a usable answer, or '' when it is.

    The flow reads a build system's OWN files to learn what a build would run
    (`ninja -t compdb`, `make -Bn`), so it needs the directory those files are
    in — not the directory the commands are run from.  Those differ, and a
    caller cannot tell from the field's name alone, so a wrong answer is
    checked for HERE rather than surfacing later as an unreadable tree.
    """
    where = src / (subdir or '.')
    markers = ('Makefile', 'makefile', 'build.ninja', 'CMakeCache.txt')
    if any((where / m).is_file() for m in markers):
        return ''
    if where.is_dir() and any((d / m).is_file() for d in where.iterdir()
                              if d.is_dir() for m in markers):
        return ''
    return (f'`tree_subdir` is {subdir!r}, and that holds no build system '
            f'(a Makefile, a build.ninja) in it or one directory down')


def _report_seam_failure(exc) -> bool:
    """Say which kind of failure this was, then report it as unresolved.

    The distinction is the point: an agent that could not be REACHED is an
    infrastructure problem, and reporting it as a refusal would send a reader
    to research the project instead of the environment.
    """
    from protocolize.agent import AgentUnavailable
    if isinstance(exc, AgentUnavailable):
        print(f'AGENT_ERROR — {exc}', file=sys.stderr)
    else:
        print(f'SEAM FAILED — {exc}', file=sys.stderr)
        if getattr(exc, 'raw', ''):
            print(f'  the answer was:\n{getattr(exc, "raw")[:2000]}',
                  file=sys.stderr)
    return False


def _native_with_seams(M: dict, args, agent, src: Path,
                       dest: Path) -> tuple[bool, str, list]:
    """Build and verify natively, opening seam A when that fails.

    The loop is bounded by the agent's own budget, and each pass must be a
    NEW answer to a NEW failure: an agent that proposes a patch which does
    not fix the build has its proposal discarded by the build failing again,
    and the signature it is cached under changes with the log. So a seam
    cannot spin, and a patch cannot be recorded unless it made the build work
    — "never record a fix that did not make the build work" is enforced by
    the loop's structure, not by a check on the agent's honesty.
    """
    ok, log, remedies = M['native'](src, args)
    if ok or agent is None:
        return ok, log, remedies
    for _ in range(MAX_SEAM_PASSES):
        before = (args.configure, args.build, args.test, args.tree_subdir,
                  tuple(args.prepare_ops or ()))
        if not _seam_native(M, args, agent, src, dest, log):
            break
        after = (args.configure, args.build, args.test, args.tree_subdir,
                 tuple(args.prepare_ops or ()))
        if after == before:
            break          # the agent changed nothing the flow can use
        ok, log, remedies = M['native'](src, args)
        if not ok:
            continue
        # A build that works is necessary and not sufficient: §A reads the
        # build system's OWN files, which live where `tree_subdir` points.
        # Measured: a real agent named a make project's commands correctly
        # (`make -C source/mis`, `make -C source/mds`) and omitted
        # `tree_subdir`, so the default `build` survived, the native build
        # PASSED, and §A then failed on a directory holding no build system.
        # §A failing has no seam — an extractor that cannot read a build is a
        # TOOL defect — so the run could not return to the seam that had just
        # answered.  Checking here, where the answer is still the subject,
        # keeps the correction at the seam that owns it.
        unreadable = _tree_subdir_without_a_build_system(src, args.tree_subdir)
        if unreadable:
            log = f'{unreadable}\n(the build itself succeeded)'
            print(f'  seam A: {unreadable} — asking again', file=sys.stderr)
            continue
        print('  seam A: the project now builds and verifies')
        break
    return ok, log, remedies


def _project_input(M: dict, src: Path, value: str | None,
                   name: str) -> Path | None:
    """The per-project input *name*, whether or not the caller named it.

    A project that declares something its tree cannot show — the preparation
    its programs need, the lines that are volatile between two runs — states
    it in a file beside its own source, which is where the skill's own
    instructions put it.  So the flow looks there rather than requiring every
    caller to remember the flag.

    This is not the "search until something turns up" that the capture
    refuses.  That rule exists because a widened search finds a NEIGHBOUR's
    declaration and verifies a corpus against the wrong project; this is one
    fixed name at one fixed place, the source root.

    Measured, and the reason this is not merely a convenience: re-proving a
    minimal sample without `--facts` silently REPLACED its `field_assertion`
    — the per-column model the project declares — with a generic line-by-line
    `text_lockstep`, and the row still read PASS.  A weaker plan that passes
    is indistinguishable from the real one unless the inputs are honoured,
    and nothing said a word.
    """
    if value:
        return M['_require_present'](M['_declared'](value, src), f'--{name}')
    beside = src / name
    if beside.is_file():
        print(f'  using {beside.relative_to(src.parent)} (the project '
              f'declares it here)')
        return beside
    return None


#: Which seam a failed STEP opens, and which answers can resolve it.
#:
#: Two steps have NO seam, and the distinction is load-bearing:
#:
#:   * `'A'` — the extractor could not read the build.  That is a TOOL defect:
#:     the fix belongs in `protocolize/`, and opening a seam here would send
#:     the agent to work around a broken tool instead of getting it repaired.
#:   * `'B-flat'` — the plan is green on the project but not on the replay.
#:     The protocol and the build disagree, which is likewise ours to fix.
#:
#: The steps that DO open a seam are the ones where the missing thing is a
#: fact about the PROJECT: a declaration nobody supplied, or a patch the
#: project needs before it can be verified.
_SEAM_FOR_STEP = {
    'plan': ('D', ('facts', 'declaration')),
    'ref': ('D', ('facts', 'declaration')),
    'B-project': ('E', ('prepare_ops', 'facts', 'declaration')),
}


def _prove_with_seams(M: dict, args, agent, dest: Path, src: Path,
                      number: int, src_name: str, remedies,
                      notes: 'str | None'):
    """Run steps 3-4, opening whatever seam a failure points at.

    The mechanical half reported WHICH STEP failed; deciding what to do about
    it is the driver's, which is why the two live apart.  A failure at a step
    with no seam returns unchanged — the flow does not invent work for an
    agent where the work is ours.
    """
    facts = _project_input(M, src, args.facts, 'facts.json')
    declaration = _project_input(M, src, args.declaration, 'declaration.json')
    work = dest / '.work' / 'agent'
    proof = None
    for _ in range(MAX_SEAM_PASSES):
        proof = M['prove_legal'](
            dest, src, src / args.tree_subdir, number, src_name, remedies,
            facts=facts, declaration=declaration, notes=notes)
        if proof.ok:
            return proof
        seam, accepts = _SEAM_FOR_STEP.get(proof.step, ('', ()))
        if not seam or agent is None:
            return proof
        print(f'  §B: step {proof.step} failed — opening seam {seam}',
              file=sys.stderr)
        before = (tuple(args.prepare_ops or ()), facts, declaration)
        if not _ask_seam(agent, seam, accepts, src, work, proof, args):
            return proof
        if args.prepare_ops and tuple(args.prepare_ops) != before[0]:
            # A patch has to be APPLIED and the build rebuilt before it can
            # be judged: the answer is a proposal, and the build is what
            # decides it.  This is also what makes "never record a patch
            # that did not make it work" structural rather than a promise.
            ok, log, _ = M['native'](src, args)
            if not ok:
                print('  the proposed patch does not build:\n' + log[-2000:],
                      file=sys.stderr)
                return proof
            # A patch that WORKED is what the protocol must record, so a
            # fresh checkout reproduces the build.  Carrying the original
            # `remedies` forward instead would apply the patch to this tree
            # and then leave it out of the protocol — the next admission
            # would build a project nobody patched.
            remedies = list(args.prepare_ops)
        facts = _seam_input(args, 'facts', facts, work)
        declaration = _seam_input(args, 'declaration', declaration, work)
        if (tuple(args.prepare_ops or ()), facts, declaration) == before:
            return proof        # the answer changed nothing usable
    return proof


def _seam_input(args, name: str, current, work: Path):
    """What a seam's answer became, if one arrived.

    A seam returns a VALUE; the capture takes a Path for a declaration and
    either for facts.  So the DRIVER writes the file — the agent still does
    not touch the tree, which is what keeps every effect of its answer
    reviewable as a file.
    """
    got = getattr(args, '_seam_' + name, None)
    if got is None:
        return current
    if name != 'declaration':
        return got              # the capture takes the object directly
    work.mkdir(parents=True, exist_ok=True)
    path = work / 'declaration.json'
    path.write_text(json.dumps(got, indent=1), encoding='utf-8')
    return path


def _failure_signature(proof) -> str:
    """A STABLE name for a failure, so the same failure reuses one answer.

    The seam cache is keyed by this, and its whole point is that the same
    failure must not produce a different answer on a second run — otherwise
    two admissions of one project record different commands and the corpus
    stops being comparable with itself.

    Measured why the obvious key does not work: the signature was
    `hash(step + detail)`, and `detail` carries what §B PRINTED — which
    includes elapsed times and timestamps. So the same failure produced a new
    signature every minute, the cache never hit, and the agent answered afresh
    each time. A cache that cannot hit is not a determinism guarantee.

    What is stable is the SHAPE of the failure: which step, how many differ,
    and which cases. The values that differ are the volatiles the question is
    about, so including them would defeat the purpose.
    """
    import re
    detail = proof.detail or ''
    counts = re.search(r'PASS: \d+, FAIL: \d+, TOTAL: \d+', detail)
    cases = sorted(set(re.findall(r'^FAIL (\S+)', detail, re.M)))[:40]
    return hashlib.sha256(
        f'{proof.step}\0{counts.group(0) if counts else ""}\0'
        f'{" ".join(cases)}'.encode()).hexdigest()[:16]


def _replace_not_extend(seam: str, args) -> str:
    """A repeated question must not be answered as a DELTA.

    Measured on a real run, and it cost the whole verdict: seam E was asked,
    the agent answered 14 volatile-line patterns, §B then failed on 7 more
    cases, and the flow asked again. The second answer held TWO patterns — the
    ones for the newly-failing lines — and the flow used it as the whole set.
    125 passed; the 7 that failed were failing on the patterns the second
    answer had dropped.

    The agent was answering the question it was given ("what is failing now"),
    which is why the fix is here and not in the prompt's wording alone: the
    flow has to say what is already in force, and ask for the whole set.
    """
    if seam != 'E':
        return ''
    current = getattr(args, '_seam_declaration', None)
    if not current:
        return ''
    comp = (current.get('comparison') or {})
    lines = '\n'.join(f'  - {p}' for p in (comp.get('drop') or ()))
    return (
        '\n\nA declaration is ALREADY IN FORCE for this project and the proof '
        'was re-run with it. It dropped the lines you named last time and the '
        'run then failed on OTHERS:\n'
        f'{lines}\n' if lines else '') + (
        'Answer with the WHOLE set this project needs — the lines above '
        'PLUS whatever you find now — not only the newly-failing ones. What '
        'you return REPLACES what is in force; an answer holding only the new '
        'lines silently un-declares the old ones.')


def _ask_seam(agent, seam: str, accepts, src: Path, work: Path,
              proof, args) -> bool:
    """Ask at *seam* and apply what comes back.  True when something changed."""
    from protocolize.agent import AgentUnavailable, SeamFailed
    agent.log_dir = work
    want = ('\n'.join(f'* "kind": "{k}"' for k in accepts)
            + '\n\nPick the one that fits what you find. If none does, answer '
              '"kind": "protocol_gap".'
            + _replace_not_extend(seam, args))
    situation = f'Step {proof.step} of the proof failed.\n{proof.detail}'
    try:
        resp = agent.ask(seam, _seam_prompt(seam, src, situation, want,
                                            _tree_census(src)), src,
                         signature=_failure_signature(proof))
    except (AgentUnavailable, SeamFailed) as exc:
        _report_seam_failure(exc)
        return False

    if resp.kind == 'protocol_gap':
        print(f'PROTOCOL_GAP — {resp.payload.get("missing")}\n'
              f'  evidence: {resp.payload.get("evidence", "")}\n'
              f'  the protocol must grow for this; the flow stops here.',
              file=sys.stderr)
        return False
    if resp.kind == 'prepare_ops':
        args.prepare_ops = list(args.prepare_ops or []) + list(
            resp.payload['ops'])
        print(f'  seam {seam}: {len(resp.payload["ops"])} patch(es) to test')
        return True
    if resp.kind in ('facts', 'declaration'):
        setattr(args, '_seam_' + resp.kind, resp.payload['value'])
        print(f'  seam {seam}: a {resp.kind} was supplied; re-running the '
              f'proof with it')
        return True
    print(f'  seam {seam} answered {resp.kind!r}, which cannot resolve '
          f'{proof.step}', file=sys.stderr)
    return False


def _row_note(proof) -> str:
    """What the row's `notes` column should say about a failure.

    `_row_note` is used by a first admission, which has no description of its
    own yet, so the failing step is the most informative thing it can carry.
    A re-prove passes `notes=None` to `_prove_with_seams` and does not record
    a failure at all — see `_readmit`.
    """
    return {
        'A': 'protocol does not replay the project',
        'plan': 'captured plan is not executable',
        'ref': 'reference run captured no goldens',
    }.get(proof.step, proof.detail.splitlines()[0] if proof.detail else 'failed')


def main(argv=None, namespace: dict | None = None) -> int:
    """Admit (or re-prove) one project. *namespace* is the mechanical half.

    A caller that IS the skill module passes its own `globals()`, so the
    driver reads the same objects the caller sees — including anything the
    caller's tests have replaced. Left out, the skill is loaded from disk.
    """
    M = fetch_namespace() if namespace is None else namespace

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
    p.add_argument('--agent-cmd', default=None,
                   help='how to invoke an agent at a seam (default: $'
                        'PROTOCOLIZE_AGENT_CMD, else `claude`). Seams are '
                        'where judgement is needed and a value cannot be read '
                        'out of the project.')
    p.add_argument('--max-agent-calls', type=int, default=6,
                   help='the ceiling on agent calls for one project '
                        '(default: 6)')
    args = p.parse_args(argv)
    args.prepare_ops = []
    if args.prepare:
        args.prepare_ops = json.loads(Path(args.prepare).read_text())

    agent = _make_agent(args)

    if args.readmit:
        return _readmit(M, args, agent)

    src_name = args.name or Path(args.source.rstrip('/')).stem
    src_name = src_name[:-4] if src_name.endswith('.git') else src_name
    n = M['next_number'](M['BENCH'])
    dest = M['BENCH'] / f'{n:03d}-{src_name}'
    print(f'admitting #{n:03d} {src_name} -> {dest}')

    if dest.exists():
        print(f'REFUSED: {dest} already exists', file=sys.stderr)
        return 1
    try:
        M['fetch'](args.source, dest / src_name)
    except M['FetchError'] as exc:
        # A failed fetch can leave `dest` behind: `shutil.copytree` creates
        # the destination BEFORE it scans the source, so a bad path leaves an
        # EMPTY admission directory.  That shell is invisible to git
        # (`benchmark/*` is ignored) but not to `next_number`, which globs
        # directories — so it silently consumes a number and the next real
        # project is admitted under the wrong one.  Measured: four aborted
        # invocations left 015/016/017/018 behind.
        shutil.rmtree(dest, ignore_errors=True)
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 1
    src = dest / src_name
    ok, log, remedies = _native_with_seams(M, args, agent, src, dest)
    if not ok:
        print('REFUSED: the project does not build and verify natively.\n' + log,
              file=sys.stderr)
        M['record_row'](n, src_name, native='no', build_protocol_a='no',
                        verify_on_project_b='no', verify_on_replay_b='no',
                        status='REFUSED',
                        notes='does not build/verify natively')
        return 1
    print('  native build + verify: OK')
    proof = _prove_with_seams(M, args, agent, dest, src, n, src_name,
                              remedies, 'fetched from source')
    if proof.ok:
        print(f'LEGAL: {dest} holds the source, protocol.json, '
              f'verification_plan.json')
        M['record_row'](n, src_name, **proof.columns)
        return 0
    print(f'NOT LEGAL — {proof.detail}', file=sys.stderr)
    M['record_row'](n, src_name, native='yes',
                    notes=_row_note(proof), **proof.columns)
    return 1


def _readmit(M: dict, args, agent) -> int:
    """Re-prove a project already in `benchmark/`.

    Split out because it is the COMPLEMENT of a first admission: it clears a
    previous build instead of leaving one, and its failure must not touch a
    row that records a proof which was actually carried out. Bundling the two
    is how the row for an established project got overwritten by a run whose
    own configuration was wrong.
    """
    dest = Path(args.source).resolve()
    # `--readmit` names the BENCHMARK FOLDER, but the inner checkout is the
    # natural thing to paste, and naming it used to die with
    # `ValueError: invalid literal for int() with base 10: 'NAS'` — a
    # traceback that says nothing about what was wrong.  A directory with no
    # leading number IS an inner checkout, so it is accepted.
    if not dest.name[:3].isdigit() and dest.parent.name[:3].isdigit():
        dest = dest.parent
    if not dest.name[:3].isdigit():
        print(f'REFUSED: {dest} is not a benchmark folder — its name does '
              f'not begin with an admission number (001-…).  Pass the '
              f'folder, e.g. benchmark/009-<name>.', file=sys.stderr)
        return 2
    src = M['_inner_source'](dest)
    number = int(dest.name[:3])
    if not src.is_dir():
        print(f'REFUSED: {src} is not a directory', file=sys.stderr)
        return 1
    print(f're-proving #{number:03d} {dest.name}')
    _clear_previous_build(src, args.tree_subdir)
    ok, log, remedies = _native_with_seams(M, args, agent, src, dest)
    if not ok:
        # NOT recorded.  A re-prove's failure is "could not re-prove", not
        # "refused": the row already holds a state that was ESTABLISHED, and
        # overwriting it destroys that fact.  Measured: an operator's
        # malformed invocation (the CMake defaults against a make project)
        # turned an established PASS into REFUSED, and nothing in the CSV said
        # the run had been misconfigured rather than the project having
        # regressed.
        print('NOT RE-PROVED: the project does not build and verify with '
              'the native commands given.\n' + log, file=sys.stderr)
        print(f'  the row for #{number:03d} is UNCHANGED — fix the '
              f'--configure/--build/--test arguments and re-run.',
              file=sys.stderr)
        return 1
    print('  native build + verify: OK')
    proof = _prove_with_seams(M, args, agent, dest, src, number,
                              dest.name.split('-', 1)[-1], remedies, None)
    if proof.ok:
        M['record_row'](number, dest.name.split('-', 1)[-1], **proof.columns)
        return 0
    print(f'NOT RE-PROVED — {proof.detail}', file=sys.stderr)
    print(f'  the row for #{number:03d} is UNCHANGED.', file=sys.stderr)
    return 1


def _clear_previous_build(src: Path, tree_subdir: str) -> None:
    """Invalidate a previous build so a re-prove configures afresh.

    A renumber (or a tool change) invalidates the previous build: its
    recorded paths name the OLD directory, so configure afresh.

    An IN-SOURCE build cannot be cleared by removing its directory — that
    directory IS the checkout — so the build system's own GENERATED files are
    removed instead and the sources are left alone.  Both halves matter:

      * Removing the whole tree deletes the project (it is the source).
      * Removing NOTHING is not neutral either: a stale cache re-runs the
        project's configure against its own old answers, and a CMake
        `set(... CACHE ... FORCE)` in the project can then OVERRIDE the flags
        this run passes.  Measured: a corpus mapped a deprecated option to a
        new one by force, so `-DNEW=EMBEDDED` kept resolving to the OLD value,
        the build linked a system library that is not installed, and it failed
        as "does not build" — a diagnosis about the project for a fact about a
        leftover file.
    """
    bd = src / tree_subdir
    generated = ((bd / 'CMakeCache.txt').is_file()
                 or (bd / 'build.ninja').is_file()
                 or (bd / 'CMakeFiles').is_dir())
    if not generated:
        return
    if bd != src:
        shutil.rmtree(bd, ignore_errors=True)
        return
    # The build system's own state, and nothing else.  A hand-written
    # `Makefile` is deliberately absent from this list: only files CMake is
    # known to generate are touched, so a project's own file can never be the
    # thing this deletes.
    for name in ('CMakeCache.txt', 'CMakeFiles', 'build.ninja',
                 '.ninja_deps', '.ninja_log', 'cmake_install.cmake'):
        target = bd / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()


if __name__ == '__main__':
    raise SystemExit(main())
