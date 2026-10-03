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
import importlib.util
import json
import shutil
import sys
from pathlib import Path

#: This file sits at the repository root, so the repo IS its directory.
REPO = Path(__file__).resolve().parent

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
    args = p.parse_args(argv)
    args.prepare_ops = []
    if args.prepare:
        args.prepare_ops = json.loads(Path(args.prepare).read_text())

    if args.readmit:
        return _readmit(M, args)

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
    ok, log, remedies = M['native'](src, args)
    if not ok:
        print('REFUSED: the project does not build and verify natively.\n' + log,
              file=sys.stderr)
        M['record_row'](n, src_name, native='no', build_protocol_a='no',
                        verify_on_project_b='no', verify_on_replay_b='no',
                        status='REFUSED',
                        notes='does not build/verify natively')
        return 1
    print('  native build + verify: OK')
    return M['prove_legal'](
        dest, src, src / args.tree_subdir, n, src_name, remedies,
        facts=M['_require_present'](M['_declared'](args.facts, src), '--facts'),
        declaration=M['_require_present'](
            M['_declared'](args.declaration, src), '--declaration'))


def _readmit(M: dict, args) -> int:
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
    ok, log, remedies = M['native'](src, args)
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
    return M['prove_legal'](
        dest, src, src / args.tree_subdir, number,
        dest.name.split('-', 1)[-1], remedies,
        facts=M['_require_present'](M['_declared'](args.facts, src), '--facts'),
        declaration=M['_require_present'](
            M['_declared'](args.declaration, src), '--declaration'))


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
