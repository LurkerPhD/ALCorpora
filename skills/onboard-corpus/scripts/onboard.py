#!/usr/bin/env python3
"""Onboard a Fortran corpus: read a protocol AND an acceptance plan out of
its own build, and prove both.

The mechanical half of the `onboard-corpus` skill, and the ONLY place a
corpus's build system is ever inspected.  It runs once per corpus; after it,
the flow owns the corpus's build entirely and nothing here is looked at
again.

It produces two artifacts, each self-verified before it is written:

* the build protocol (``extract_protocol``) -- proven by rebuilding from it;
* the acceptance plan (``capture_verification``) -- proven by
  ``problems()`` (refuses a vacuous plan) and by running the plan on the
  reference build (``bench_cli``).

Two things it refuses to do:

* guess.  ``extract_protocol`` reads through the build system's own
  subtools and raises with the specific gap when the build cannot be
  expressed as a protocol.  A refusal is a finding to record, never
  something to work around by hand-editing a protocol -- one that merely
  looks right fails inside a build, far from the cause.
* write a config it cannot read back.  A corpus config carries more than its
  build (the benchmark script, the pipeline arguments, the module
  directories), so this MERGES rather than replaces.  Overwriting would look
  like it worked -- the build is generic now, so nothing complains -- while
  the corpus quietly lost the wiring that feeds the correctness signal.

Exit status is the answer: 0 proven, 1 refused or not proven, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from protocolize.onboard.extract import (  # noqa: E402
    ExtractionError, capture_verification, extract_protocol,
)

#: Flags applied to EVERY converted unit, whichever corpus.  They are
#: deepf2c-wide build policy, not a corpus fact: the alignment is the fix for
#: a measured phase-layout regression and the BLAS switch keeps a foreign
#: library off the link line — both stated in AGENTS.md.  Declared HERE, where
#: the protocol is made, rather than read back from a corpus config: the
#: protocol must not depend on a file the flow owns, or the same tree
#: onboards to two different protocols.
DEFAULT_POLICY_FLAGS = ('-falign-functions=64', '-DF2C_NO_BLAS')


def _prepare_is_done(op: dict, base: Path) -> bool:
    """The corpus's own test that *op* has already happened.

    ``check.path`` is relative to *base* (the project's own checkout): a
    submodule path, a stale file a setup step removes.  ``exists`` defaults
    to true -- the op is done when the path is there -- and may be false for
    a check that is satisfied by the path being GONE.
    """
    path = (op.get('check') or {}).get('path')
    if not path:
        return True
    target = base / path
    return target.exists() if op['check'].get('exists', True) else not target.exists()


def _run_prepare(proto, base: Path) -> int:
    """Bring the corpus up to buildable, running each op AT MOST ONCE.

    The check is what makes this safe to call on every onboarding: when the
    submodule is already there or the patch already landed, the op is
    skipped, so a repeat pays nothing.  That is the whole point of putting
    this in the protocol rather than in a setup script -- the infrastructure
    persists across the repeated builds and runs that follow.

    A failing op is reported and stops the run: a build attempted on a
    half-prepared tree fails far from here, naming neither the patch nor the
    file it was meant to fix.
    """
    for op in proto.prepare or ():
        if _prepare_is_done(op, base):
            print(f'  prepare[{op.get("op")}]: already done, skipping')
            continue
        print(f'  prepare[{op.get("op")}]: {op["run"]}')
        r = subprocess.run(op['run'], shell=True, cwd=str(base))
        if r.returncode:
            print(f'  prepare[{op.get("op")}] FAILED (exit {r.returncode})',
                  file=sys.stderr)
            return r.returncode
    return 0


def _parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('build_tree',
                    help='the directory the build system runs in — the tree '
                         'it left behind, which is what the protocol is read '
                         'back out of')
    ap.add_argument('--tree-root', default=None,
                    help='what ${TREE} denotes (default: the build tree\'s '
                         'parent, which is where a CMake out-of-source build '
                         'puts a corpus\'s sources)')
    ap.add_argument('--out', default=None,
                    help='where to write the protocol (default: '
                         '<tree-root>/protocol_fortran.json)')
    ap.add_argument('--config', default=None,
                    help='corpus config to MERGE the protocol path into')
    ap.add_argument('--comment', default='')
    ap.add_argument('--declaration', default=None,
                    help='the file where the corpus states its comparison '
                         'rules (volatile lines, numeric slack).  Passed IN '
                         'rather than searched for: a search that widened '
                         'until it found something would find a neighbour\'s.')
    ap.add_argument('--facts', default=None,
                    help='JSON of facts the corpus declares that its tree '
                         'does not state machine-readably (restart chains, '
                         'preparation, artifact suffixes).')
    ap.add_argument('--prepare', default=None,
                    help='JSON list of one-time prepare ops the TREE cannot '
                         'state (a portability patch the corpus needs to build '
                         'at all).  Each op needs a `check` and a `run`; the '
                         'submodule ops are derived from the project\'s own '
                         '.gitmodules and appended, so they are not listed '
                         'here.')
    ap.add_argument('--build', default=None,
                    help='JSON object of the `build` DECLARATIONS no recorded '
                         'command carries (flags.optimize, flags.policy).  '
                         'Default policy flags are deepf2c-wide and supplied '
                         'by this skill; the include/module directories are '
                         'OBSERVED from the build and never declared here.')
    ap.add_argument('--skip-plan', action='store_true',
                    help='capture ONLY the build protocol.  For a corpus that '
                         'declares no acceptance yet; the flow then has no '
                         'plan to run it against.')
    ap.add_argument('--project-root', default=None,
                    help="the project's own checkout, when it is not the "
                         "build tree's neighbour.  Read to learn which "
                         "directories the project VENDORED (its "
                         "'.gitmodules'), so a corpus vendored together with "
                         "the libraries it links does not convert them.")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse(argv if argv is not None else sys.argv[1:])
    tree = Path(args.build_tree).resolve()
    root = Path(args.tree_root).resolve() if args.tree_root else tree.parent

    # NO CHECK HERE for a particular build system.  Which trees can be read
    # is the extractor's to know, and a second opinion in the entry point
    # would be a list of magic file names that goes stale the first time a
    # build system is added -- while looking authoritative the whole time.
    # The extractor refuses with the gap named, which is the useful answer.
    print(f'extracting a protocol from {tree} ...')
    declared = None
    if args.prepare:
        declared = json.loads(Path(args.prepare).read_text(encoding='utf-8'))
        if not isinstance(declared, list):
            raise SystemExit('--prepare must be a JSON LIST of ops')
    # The DECLARATIONS the `build` section carries and no recorded command
    # does -- the policy flags every unit gets, the converted-level map --
    # come from the SKILL, not from the flow's config.  Reading them from a
    # config made the protocol depend on a file the flow owns, so the same
    # corpus read twice (once with a config, once without) produced two
    # protocols and the second looked complete.  Measured: the policy list
    # went `[]` -> `['-falign-functions=64', '-DF2C_NO_BLAS']` on the same
    # tree by the mere presence of `f2c_<corpus>.json`.  A declaration the
    # skill cannot see is a declaration it must be TOLD, so it is an
    # argument with the deepf2c-wide defaults, exactly like `--prepare`.
    build: dict = {}
    if args.build:
        build = json.loads(Path(args.build).read_text(encoding='utf-8'))
        if not isinstance(build, dict):
            raise SystemExit('--build must be a JSON object')
    flags = dict(build.get('flags') or {})
    flags.setdefault('policy', list(DEFAULT_POLICY_FLAGS))
    build['flags'] = flags
    try:
        proto = extract_protocol(tree, tree_root=root, comment=args.comment,
                                 project_root=args.project_root,
                                 prepare=declared, cfg=build)
    except ExtractionError as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        print('Record the gap and fix the extraction.  Do NOT hand-edit a '
              'protocol: one that merely looks right fails far from here.',
              file=sys.stderr)
        return 1

    problems = proto.problems()
    if problems:
        print(f'REFUSED: the protocol has {len(problems)} problem(s):',
              file=sys.stderr)
        for w in problems[:20]:
            print(f'  - {w}', file=sys.stderr)
        return 1

    variants: dict = {}
    for e in proto.compilation:
        variants[e.variant] = variants.get(e.variant, 0) + 1
    print(f'  {len(proto.compilation)} units {variants}, '
          f'{len(proto.generated_inputs)} generated sources, '
          f'{len(proto.products)} products, 0 problems')

    out = Path(args.out) if args.out else root / 'protocol_fortran.json'
    proto.save(out)
    print(f'wrote {out}')

    # Idempotent infrastructure, run once and skipped ever after.  It is
    # placed here, after the protocol, because that is where the full op list
    # exists (the submodule ops are derived from the project during
    # extraction) -- and because onboarding runs on an already-built tree the
    # common outcome is that every check holds and nothing runs.
    base = (Path(args.project_root).resolve() if args.project_root
            else (tree.parent if tree.name == 'build' else tree))
    if proto.prepare:
        rc = _run_prepare(proto, base)
        if rc:
            return rc

    if not args.skip_plan:
        rc = _capture_and_prove_plan(proto, tree, root, args)
        if rc:
            return rc

    if args.config:
        cfg_path = Path(args.config)
        cfg: dict = {}
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        cfg.setdefault('comment',
                       'Extracted from the corpus\'s own build by the '
                       'onboard-corpus skill.  Regenerate with that skill; '
                       'do not hand-edit.')
        cfg['protocol'] = str(out.relative_to(REPO)) if out.is_relative_to(
            REPO) else str(out)
        # The protocol is the AUTHORITY and the config is generated FROM it,
        # never the other way: the two build declarations are copied in this
        # direction so a legacy builder that still reads the config cannot
        # drift from the protocol the flow now reads.  The include/module
        # directories are deliberately NOT copied: they are observed from
        # the build's own commands, and a config's hand-kept list of them is
        # exactly the second, staler statement this section replaces.
        flags = (proto.build or {}).get('flags') or {}
        if flags.get('policy'):
            cfg['policy_flags'] = list(flags['policy'])
        if flags.get('optimize'):
            cfg['optimize'] = dict(flags['optimize'])
        cfg_path.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
        print(f'merged into {cfg_path}')

    return 0


def _capture_and_prove_plan(proto, build: Path, root: Path, args) -> int:
    """Capture the corpus's acceptance plan, and PROVE it is not vacuous.

    The build half is proven by ``extract_protocol`` (it rebuilds from the
    protocol).  The acceptance half has its own two checks, because a plan can
    be well formed and still judge nothing:

    * ``VerificationPlan.problems()`` REFUSES a plan that could produce a
      verdict with nothing behind it — no minimum, a process run that names
      no input, a golden of ``none`` with no stamp.
    * the plan is RUN against the reference (``bench_cli``'s exit status is
      the verdict).  A plan that does not pass on the corpus's own build does
      not judge the corpus.  When the reference is not runnable the run is
      reported as NOT DONE, never as passed.
    """
    from protocolize.verification_plan import VerificationPlan

    try:
        data = capture_verification(proto, build, root,
                                    project_root=args.project_root or None,
                                    declaration=args.declaration,
                                    facts=args.facts)
    except (ValueError, ExtractionError) as exc:
        print(f'REFUSED: no acceptance plan: {exc}', file=sys.stderr)
        print('A corpus that declares no way to verify a run cannot be driven '
              'by the flow.  (--skip-plan writes only the build protocol.)',
              file=sys.stderr)
        return 1

    plan = VerificationPlan.from_json(data)
    problems = plan.problems()
    if problems:
        print(f'REFUSED: the verification plan has {len(problems)} problem(s):',
              file=sys.stderr)
        for w in problems[:20]:
            print(f'  - {w}', file=sys.stderr)
        return 1

    plan_path = root / 'verification_plan.json'
    plan.save(plan_path)
    print(f'wrote {plan_path}')
    return _run_plan_against_reference(plan_path)


def _run_plan_against_reference(plan_path: Path) -> int:
    """Run the freshly captured plan on the reference build.

    ``bench_cli``'s exit status is the plan's verdict: 0 when the plan's own
    summary is met.  A non-zero verdict REFUSES the plan — it does not judge
    the corpus it was captured from.  A reference that cannot be run at all
    (not built or not benchmarked) is reported as NOT verified, which is not
    a pass.

    It is pointed at the directory the plan was WRITTEN to, not the raw
    build tree: ``bench_cli`` loads ``<dir>/verification_plan.json`` and
    discovers the tree to run under it, so passing the build tree's own
    subdirectory (CMake's ``build/``) made it look for a plan one level
    below where onboarding wrote it — the plan was correct and the verdict
    was ``no verification_plan.json``.
    """
    from protocolize.edd import bench_cli

    tree = plan_path.parent
    print(f'self-verifying the plan on the reference ({tree}) ...')
    try:
        rc = bench_cli.main([str(tree)])
    except Exception as exc:  # noqa: BLE001 — report, never crash onboarding
        print(f'  the plan could NOT run on the reference: {exc}',
              file=sys.stderr)
        print('  NOT self-verified — build and benchmark the corpus, then '
              're-run onboarding.', file=sys.stderr)
        return 1
    if rc == 0:
        print('  plan verdict on the reference: PASS')
        return 0
    print(f'  plan verdict on the reference: FAIL (exit {rc})', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
