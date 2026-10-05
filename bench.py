#!/usr/bin/env python3
"""The fixed tools: one entry point, one atomic operation per subcommand.

Every operation this project performs on a corpus is here, spelled once, so
that a caller — a person, a script, or an agent — RUNS the operation instead
of writing a new one. That is the point: an agent that improvises a way to
"find the tree" or "clear the build" produces a different answer on each run,
and a benchmark whose own procedure is not reproducible cannot be used to
judge reproducibility. So the operations are fixed, and CLAUDE.md makes using
them mandatory.

Nothing here is a new mechanism. Each subcommand DELEGATES to the module that
already owns the behaviour — `admit.py` drives the flow, `verify.cli` runs a
plan, `alcorpora.volatiles` measures what two runs disagree on. This file
only gives them one name and one place to be found.

    bench.py tree    DIR        which directory is the plan's ${TREE}
    bench.py wipe    DIR...     restore a checkout to what git brought
    bench.py plan    DIR        the plan's problems, and the modes it declares
    bench.py extract  TREE [--out F]   §A: read the build protocol OUT of a
                                built tree, and prove it replays
    bench.py capture  DIR --declaration F [--facts F]
                                read the verification protocol out of the
                                project's own declaration
    bench.py cases    DIR       the case ids the plan discovers
    bench.py verify  DIR        §B: run the plan, print the verdict
    bench.py evidence DIR [--shift-date]
                                run the reference TWICE and report the line
                                shapes that moved and are NOT yet declared
    bench.py row     DIR        this project's line in benchmark.csv
    bench.py fetch   URL [--…]  a FIRST admission: number, fetch, prove
    bench.py admit   DIR [--…]  re-prove one already in benchmark/

Exit status: 0 on success, 1 on a failed verdict, 2 on a usage error. A
subcommand that cannot run at all says so on stderr and returns 1 — never a
traceback, because a caller branching on the exit code must not have to parse
a stack trace to learn that a path was wrong.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BENCH = REPO / 'benchmark'

#: What `wipe` asks, and the only thing it asks: does git own this path?
#:
#: A benchmark folder holds the checked-out project PLUS everything the flow
#: produced beside it — the protocol, the plan, the run's results, and the
#: per-project research results (the ops that made the build work, the facts a
#: program needs before it will run, the comparison rules, the test driver).
#: Those last four are OUTPUT: they were derived FOR that project, and leaving
#: them in place makes a "regression" replay an answer it wrote down earlier
#: instead of re-deriving it.  Naming them in a list here was the older shape,
#: and the list was WRONG — it kept `facts.json`/`declaration.json`/
#: `prepare-ops.json`/`native-verify.sh` while calling itself "remove every
#: artifact this tool generated", so the cleanup and its own description
#: disagreed and the regression consumed its own output.
#:
#: So there is no list.  A checkout is restored to HEAD (submodules included);
#: everything git does not own is removed.  Which paths those are is git's
#: answer, asked per path, and it cannot drift from the checkout the way a
#: name list does.


# ── atomic operations ─────────────────────────────────────────

def tree_of(dest: Path) -> Path:
    """The directory a plan's `${TREE}` denotes.

    DELEGATES to the flow's own `_inner_source`, which is the one place that
    decides it. Re-deriving the rule here was measured to get it WRONG: a
    checkout whose build files sit a level down and whose top level carries no
    build-system marker (a hand-written make project with its Makefiles under
    `source/`) was reported as its own folder rather than the checkout, and
    the run then found no cases. One question, one implementation.
    """
    import admit
    return Path(admit.fetch_namespace()['_inner_source'](Path(dest).resolve()))


def cmd_tree(args: list[str]) -> int:
    if len(args) != 1:
        print('usage: bench.py tree DIR', file=sys.stderr)
        return 2
    print(tree_of(Path(args[0])))
    return 0


def _git_brought(path: Path) -> bool:
    """Whether git brought *path* — a checkout, or a file a repository tracks.

    Asked of git rather than by a list of names, because "what is part of the
    checkout" is git's question and a list here is a second answer that drifts
    from the first.  A submodule is a checkout the superproject points at (its
    ``.git`` is a FILE, not a directory) and is kept; a sample's source is
    tracked by the repository the benchmark itself lives in.
    """
    if (path / '.git').exists():
        return True
    r = subprocess.run(['git', '-C', str(path.parent), 'ls-files', '--',
                        path.name], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _checkouts_in(folder: Path) -> list:
    """The working trees *folder* holds: itself, or its directories."""
    if (folder / '.git').exists():
        return [folder]
    return [c for c in sorted(folder.iterdir()) if (c / '.git').exists()]


def _restore_checkout(root: Path) -> None:
    """Put a checkout back to what git brought, submodules included.

    ``clean -fdx``, never a bare ``clean -f``: this tool's artifacts beside a
    project are IGNORED files, and a bare form leaves ignored files alone —
    measured, it removed the untracked-but-not-ignored ones and left exactly
    the artifacts wanted gone.

    The superproject's reset does not descend into its submodules (measured: a
    file modified inside one survived ``reset --hard`` + ``clean -fdx`` at the
    top), so the same pair is run in each through ``foreach --recursive``.
    """
    print(f'  restore {_short(root)}')
    for argv in (['reset', '--hard'], ['clean', '-fdx']):
        subprocess.run(['git', '-C', str(root), *argv],
                       capture_output=True, text=True)
    subprocess.run(['git', '-C', str(root), 'submodule', 'foreach',
                    '--recursive', 'git reset --hard && git clean -fdx'],
                   capture_output=True, text=True)


def _restore_tracked(path: Path) -> None:
    """Put a tracked file back to HEAD — the same promise the checkouts get.

    A benchmark folder can hold a tracked file the flow might have patched
    (a sample's own declaration).  Leaving a modification there would let the
    next run's `check` find its own earlier work and call the op applied,
    which is the same "the cleanup consumed its own output" failure in a
    smaller place.
    """
    subprocess.run(['git', '-C', str(path.parent), 'checkout', '--', path.name],
                   capture_output=True, text=True)


def cmd_wipe(args: list[str]) -> int:
    """Return a benchmark folder to its ORIGINAL state: what git brought.

    Nothing the flow produced survives — not the protocol or the plan (which
    are regenerated), and not the per-project research results either (the
    build-failure ops, the facts a program needs to run, the comparison rules,
    the test driver).  Those are derived FOR the project, so a regression that
    keeps them is not re-deriving anything: it is re-running a written-down
    answer, and it cannot tell a flow that still derives it from one that has
    forgotten how.  Re-deriving is the whole point of a regression.

    What stays is therefore decided by two questions, both answered by the
    flow's own machinery rather than by a list of names:

      * is it the project? — ``tree_of`` is the flow's own answer to "which
        directory is this project", and it is kept even when git brought
        nothing there (measured: one corpus checkout has no ``.git`` at all);
      * did git bring it? — a working tree of its own, or a path tracked by
        an enclosing repository.  Those are RESTORED to HEAD, submodules
        included, which is what a fresh clone would have produced.

    Everything else is removed.  ``.git`` itself is always kept — it is the
    repository, not a path inside it.
    """
    if not args:
        print('usage: bench.py wipe DIR...', file=sys.stderr)
        return 2
    for a in args:
        d = Path(a).resolve()
        if not d.is_dir():
            print(f'{d} is not a directory', file=sys.stderr)
            return 1
        # 1. What git brought, back to what git brought.
        keep = {tree_of(d).resolve()}
        for c in _checkouts_in(d):
            _restore_checkout(c)
            keep.add(c.resolve())
        # 2. Everything git does not own goes.
        for child in sorted(d.iterdir()):
            if child.resolve() in keep or child.name == '.git':
                continue
            if _git_brought(child):
                _restore_tracked(child)
                continue
            print(f'  rm {_short(child)}')
            if child.is_dir():
                _rmtree(child)
            else:
                child.unlink()
    return 0


def _short(p: Path) -> str:
    """*p* relative to the repo when it is under it, else absolute.

    A caller can point this at any directory, and a path outside the repo has
    no relative form — `relative_to` would raise where a printed name was all
    that was wanted.
    """
    try:
        return str(Path(p).relative_to(REPO))
    except ValueError:
        return str(p)


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def cmd_plan(args: list[str]) -> int:
    """Validate a plan and say which comparison modes it declares.

    The declared-mode counts are what make this useful to a caller writing
    rules: `0 mask` says the plan has no in-line trimming, which is exactly
    the situation that lets a clock reading and a judged value share a line
    and be dropped together.
    """
    if len(args) != 1:
        print('usage: bench.py plan DIR', file=sys.stderr)
        return 2
    from alcorpora.verification_plan import VerificationPlan
    from alcorpora.volatiles import declared_modes

    d = Path(args[0]).resolve()
    p = d / 'verification_plan.json'
    if not p.is_file():
        print(f'no verification_plan.json under {d}', file=sys.stderr)
        return 1
    plan = VerificationPlan.load(p)
    problems = plan.problems()
    drop, keep, mask = declared_modes(plan)
    print(f'{d.name}: '
          f'{len(drop)} drop / {len(keep)} keep / {len(mask)} mask pattern(s)')
    for text in drop:
        print(f'  drop  {text!r}')
    for text in keep:
        print(f'  keep  {text!r}')
    for rx, repl in mask:
        print(f'  mask  {rx!r} -> {repl!r}')
    if problems:
        print(f'NOT EXECUTABLE — {len(problems)} problem(s):', file=sys.stderr)
        for x in problems:
            print(f'  {x}', file=sys.stderr)
        return 1
    print('  plan: executable')
    return 0


def cmd_verify(args: list[str]) -> int:
    """Run §B against a project's plan, on whatever is on disk now.

    A verdict, printed and returned as the exit code. This does NOT clear
    anything: `bench.py wipe` and `admit.py --readmit` do that, and a verify
    that silently wiped would make "it passed" mean "it passed on whatever a
    previous run left".
    """
    if not args:
        print('usage: bench.py verify DIR', file=sys.stderr)
        return 2
    d = Path(args[0]).resolve()
    if not (d / 'verification_plan.json').is_file():
        print(f'no verification_plan.json under {d} — nothing to verify',
              file=sys.stderr)
        return 1
    return subprocess.run(
        [sys.executable, '-m', 'alcorpora.verify.cli', str(d),
         '--tree', str(tree_of(d)), '--origin', str(d / 'origin_release')],
        cwd=str(REPO)).returncode


def cmd_evidence(args: list[str]) -> int:
    """What two reference runs DISAGREE on, minus what the plan already covers.

    The measurement a comparison rule is written against. Delegates to
    `alcorpora.volatiles`, which owns the definition.
    """
    from alcorpora import volatiles
    if not args:
        print('usage: bench.py evidence DIR [--shift-date]', file=sys.stderr)
        return 2
    d = Path(args[0]).resolve()
    rest = [str(d), '--tree', str(tree_of(d))] + list(args[1:])
    return volatiles.main(rest)


def cmd_row(args: list[str]) -> int:
    """This project's line in benchmark/benchmark.csv, or a plain refusal."""
    if len(args) != 1:
        print('usage: bench.py row DIR', file=sys.stderr)
        return 2
    import csv
    d = Path(args[0]).resolve()
    if not d.name[:3].isdigit():
        print(f'{d.name} carries no admission number', file=sys.stderr)
        return 1
    n = int(d.name[:3])
    csv_path = BENCH / 'benchmark.csv'
    if not csv_path.is_file():
        print('no benchmark/benchmark.csv', file=sys.stderr)
        return 1
    with csv_path.open(encoding='utf-8', newline='') as fh:
        for row in csv.DictReader(fh):
            if row.get('number') == f'{n:03d}':
                for k, v in row.items():
                    print(f'{k:>20}: {v}')
                return 0
    print(f'no row for {n:03d}', file=sys.stderr)
    return 1


def cmd_extract(args: list[str]) -> int:
    """§A: read the build protocol OUT of a built tree, and PROVE it replays.

    The step that used to be an inline `python3 -c "…extract_protocol…"` in a
    skill document. It is deterministic given a tree — which backend, which
    units, which products — so it is a command, not a paragraph.
    """
    import argparse
    from alcorpora.onboard.extract import ExtractionError, extract_and_prove

    p = argparse.ArgumentParser(prog='bench.py extract',
                                description=cmd_extract.__doc__.splitlines()[0])
    p.add_argument('build_dir', help='the build tree')
    p.add_argument('--tree-root', type=Path, default=None,
                   help='the tree the units are rooted in (default: build dir)')
    p.add_argument('--out', type=Path, default=None,
                   help='where to write the protocol (default: <build>/protocol.json)')
    p.add_argument('--work', type=Path, default=None,
                   help='scratch for the proof (default: <build>/.work)')
    a = p.parse_args(args)

    build = Path(a.build_dir).resolve()
    tree = Path(a.tree_root).resolve() if a.tree_root else build
    work = Path(a.work) if a.work else build / '.work'
    try:
        proto, report = extract_and_prove(build, tree_root=tree, work_root=work)
    except ExtractionError as exc:
        print(f'NOT LEGAL — §A failed (the protocol does not replay the '
              f'project):\n{exc}', file=sys.stderr)
        return 1
    out = Path(a.out) if a.out else build / 'protocol.json'
    proto.save(out)
    print(f'§A: {report.compared} product(s) compared, OK')
    print(f'wrote {out}')
    return 0


def cmd_capture(args: list[str]) -> int:
    """Read the VERIFICATION protocol out of a project's own declaration.

    The other inline `python3 -c` step. Deterministic given a protocol and a
    declaration: it stages, discovers cases and builds the assertions, then
    refuses if `problems()` is non-empty — a plan that merely loads is not a
    plan that judges.
    """
    import argparse
    from alcorpora.onboard.extract import capture_verification
    from alcorpora.private_protocol import Protocol
    from alcorpora.verification_plan import VerificationPlan

    p = argparse.ArgumentParser(prog='bench.py capture',
                                description=cmd_capture.__doc__.splitlines()[0])
    p.add_argument('build_dir')
    p.add_argument('--declaration', type=Path, required=True,
                   help='the file where the project states its comparison rules')
    p.add_argument('--protocol', type=Path, default=None,
                   help='the build protocol (default: <build>/protocol.json)')
    p.add_argument('--tree-root', type=Path, default=None)
    p.add_argument('--project-root', type=Path, default=None,
                   help="the project's own root, for support files "
                        '(default: the tree root)')
    p.add_argument('--facts', type=Path, default=None,
                   help='what the project declares its programs need to RUN')
    p.add_argument('--out', type=Path, default=None)
    a = p.parse_args(args)

    build = Path(a.build_dir).resolve()
    tree = Path(a.tree_root).resolve() if a.tree_root else build
    proto_path = Path(a.protocol) if a.protocol else build / 'protocol.json'
    if not proto_path.is_file():
        print(f'no build protocol at {proto_path} — run `bench.py extract` first',
              file=sys.stderr)
        return 1
    proto = Protocol.load(proto_path)
    try:
        data = capture_verification(
            proto, build, tree,
            project_root=Path(a.project_root) if a.project_root else tree,
            declaration=a.declaration, facts=a.facts)
    except ValueError as exc:
        # `capture_verification` REFUSES rather than returning an empty plan —
        # "no registered tests, no case list, no per-case directory" is a
        # finding, not a crash — and its message names what is missing.  A
        # caller branching on the exit code must not have to read a stack
        # trace to learn that.
        print(f'the project declares no way to verify a run:\n{exc}',
              file=sys.stderr)
        return 1
    data['protocol'] = str(proto_path.name)
    plan = VerificationPlan.from_json(data)
    problems = plan.problems()
    if problems:
        print(f'the captured plan is NOT EXECUTABLE — {len(problems)} problem(s):',
              file=sys.stderr)
        for x in problems:
            print(f'  {x}', file=sys.stderr)
        return 1
    out = Path(a.out) if a.out else build / 'verification_plan.json'
    plan.save(out)
    print(f'wrote {out} ({len(plan.assertions)} assertion(s))')
    return 0


def cmd_cases(args: list[str]) -> int:
    """The case ids a plan discovers, one per line.

    Deterministic and cheap, and useful on its own: a plan that discovers
    ZERO cases is the failure a whole golden comparison hides behind.
    """
    if not args:
        print('usage: bench.py cases DIR', file=sys.stderr)
        return 2
    d = Path(args[0]).resolve()
    if not (d / 'verification_plan.json').is_file():
        print(f'no verification_plan.json under {d}', file=sys.stderr)
        return 1
    return subprocess.run(
        [sys.executable, '-m', 'alcorpora.verify.cli', str(d),
         '--tree', str(tree_of(d)), '--list'],
        cwd=str(REPO)).returncode


def cmd_fetch(args: list[str]) -> int:
    """Admit a project for the FIRST time: number it, fetch it, prove it.

    The counterpart of `admit` (which re-proves one already in `benchmark/`).
    Delegates to `admit.py` without `--readmit`, so a first admission and a
    re-prove are the same flow reached two ways — not two implementations.
    """
    if not args:
        print('usage: bench.py fetch <url-or-path> [--name … --build … …]',
              file=sys.stderr)
        return 2
    import admit
    return admit.main(list(args))


def cmd_admit(args: list[str]) -> int:
    """Re-prove a project already in benchmark/. Delegates to `admit.py`.

    Arguments after DIR are passed straight through, so this is the same
    entry point the regression uses and not a second implementation of it.
    """
    if not args:
        print('usage: bench.py admit DIR [--configure … --build … …]',
              file=sys.stderr)
        return 2
    import admit
    return admit.main(['--readmit'] + list(args))


# ── dispatch ──────────────────────────────────────────────────

_COMMANDS = {
    'extract': cmd_extract,
    'capture': cmd_capture,
    'cases': cmd_cases,
    'fetch': cmd_fetch,
    'tree': cmd_tree,
    'wipe': cmd_wipe,
    'plan': cmd_plan,
    'verify': cmd_verify,
    'evidence': cmd_evidence,
    'row': cmd_row,
    'admit': cmd_admit,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ('-h', '--help', 'help'):
        print(__doc__.split('\n\n')[0])
        print('\ncommands: ' + ', '.join(sorted(_COMMANDS)))
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    fn = _COMMANDS.get(cmd)
    if fn is None:
        print(f'unknown command {cmd!r}; known: '
              + ', '.join(sorted(_COMMANDS)), file=sys.stderr)
        return 2
    return fn(rest)


if __name__ == '__main__':
    raise SystemExit(main())
