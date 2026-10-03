"""Benchmark block: run the configured benchmark suite, parse counts,
manage the origin (generated golden) directory.
"""


import os
import shutil
import signal
import subprocess
import sys
import time

from pathlib import Path

from . import suite
from . import counts as count
from . import log
from .log import Action
from .suite import PlanRunError
from protocolize.output_layout import RESULTS_DIR
# The base a config's relative paths resolve against when the config
# does not name its own directory.  The process's cwd, because this
# tool is not tied to any one checkout.
_CONFIG_BASE = Path.cwd()


#: The acceptance plan, beside the protocol it belongs to.  Named for what it
#: IS rather than for the corpus: the flow may not know a corpus's name, and a
#: plan that has to be found by guessing one is a plan the mechanism is
#: coupled to.
PLAN_NAME = 'verification_plan.json'


def plan_path(cfg: dict) -> Path:
    """Where this corpus's acceptance plan would be, whether or not it is."""
    proto = Path(cfg.get('protocol', ''))
    if not proto.is_absolute():
        proto = Path(cfg.get('_config_dir', _CONFIG_BASE)) / proto
    return proto.parent / PLAN_NAME


def load_plan(cfg: dict):
    """The corpus's acceptance plan, or ``None`` if it has none yet.

    ``None`` is a real answer: a corpus that has not been onboarded keeps the
    old path.  What must NOT happen is a corpus that HAS a plan quietly using
    the old path, so the callers below dispatch on this rather than on
    availability of anything else.
    """
    path = plan_path(cfg)
    if not path.is_file() and not Path(str(path) + '.gz').is_file():
        return None
    from protocolize.verification_plan import VerificationPlan
    return VerificationPlan.load(path)


def _spell_dir(ref: str, build_dir: Path) -> Path:
    """One candidate root, with its bindings spelled against *build_dir*."""
    text = str(ref).replace('${TREE}', str(build_dir))
    text = text.replace('{tree}', str(build_dir)).replace('{target}',
                                                          str(build_dir))
    return Path(text)


def resolve_product(plan, build_dir: Path) -> Path:
    """Where the product under test is, from the plan's declared candidates.

    The plan declares CANDIDATES (``target.dirs``) because the same plan runs
    against two builds that put their artifacts in different places — the
    project's own build and the generated flat build.  Each is searched for a
    file of the product's name; exactly one hit is the answer, and MORE than
    one is refused rather than picked silently (the same rule as everywhere
    else in this tool).

    With no candidates declared, the conventional places are tried, so a plan
    captured before this field still resolves.
    """
    name = plan.target.product
    if not name:
        return build_dir / 'bin'
    refs = plan.target.dirs or ('{tree}/bin', '{tree}/curr/bin')
    hits = [p for p in (_spell_dir(r, build_dir) / name for r in refs)
            if p.is_file()]
    if len(hits) > 1:
        raise PlanRunError(
            f'the product {name!r} is in more than one of the plan\'s '
            f'candidate directories ({", ".join(str(h) for h in hits)}) — '
            f'which one is under test is not something to guess')
    if hits:
        return hits[0]
    # Nothing found: name the first candidate so the failure says where it
    # looked rather than handing back a path from nowhere.
    return _spell_dir(refs[0], build_dir) / name


def resolve_tree(plan, build_dir: Path) -> Path:
    """Where a tree target's registrations are, from the plan's candidates.

    The registrations live where the build system put them, and the same plan
    runs on two builds whose configure directories differ — so the plan
    declares the candidates and the one holding the registry is the answer.
    """
    refs = plan.target.dirs or ('{tree}/build', '{tree}')
    hits = [p for p in (_spell_dir(r, build_dir) for r in refs)
            if (p / 'CTestTestfile.cmake').is_file()]
    if len(hits) > 1:
        raise PlanRunError(
            f'the build system\'s registrations are in more than one of the '
            f'plan\'s candidate directories '
            f'({", ".join(str(h) for h in hits)}) — refusing to guess')
    if hits:
        return hits[0]
    return _spell_dir(refs[0], build_dir)


def plan_env(plan, build_dir: Path, cfg: dict,
             origin_dir: Path | None = None,
             exe_path: Path | None = None) -> 'suite.Env':
    """The run-time roots a plan is executed against.

    ``target`` is the plan's own answer, not the harness's: a product corpus's
    target is that product, and a tree corpus's is the tree.  The harness used
    to hand every corpus ``<tree>/bin/<binary_name>``, which for the one
    corpus that is a library named a path that does not exist.
    """
    build_dir = Path(build_dir)
    if exe_path:
        # The caller's answer wins when it has one: it has already resolved
        # the target from the plan's candidates (against the RUN's tree, not
        # the directory the plan file happens to sit in).
        target = Path(exe_path)
    elif plan.target.kind == 'product':
        target = resolve_product(plan, build_dir)
    else:
        # A tree corpus is judged where the build system put its
        # registrations (see resolve_tree).
        target = resolve_tree(plan, build_dir)
    return suite.Env(
        target=target, tree=build_dir,
        origin=Path(origin_dir) if origin_dir else Path(cfg['target_dir']),
        results=build_dir / RESULTS_DIR,
        # The MEASUREMENT width, not the build policy.  A suite corpus's width
        # is part of its measurement — its drivers are CPU-bound and contend,
        # so a baseline recorded at one width and compared against a run at
        # another silently invalidates the ratio.  The plan declares it
        # (`run.jobs`); `_resolve_jobs` is the *build* policy ("every logical
        # core") and is only the fallback for a plan that declares none.
        jobs=plan.run.jobs or max(1, os.cpu_count() or 1),
        protocol_tests=protocol_tests(cfg))


def protocol_tests(cfg: dict) -> list:
    """The build system's registered tests, from the committed protocol.

    Read here rather than passed around because the plan names them
    (``cases.kind: build_system_tests``) and the protocol is where they live.
    An absent protocol yields an empty list: a corpus whose cases are its
    build system's registrations and which has no protocol has no cases, and
    ``require_min`` is what makes that visible.
    """
    path = Path(cfg.get('protocol', ''))
    if not path.is_absolute():
        path = Path(cfg.get('_config_dir', _CONFIG_BASE)) / path
    if not path.is_file() and not Path(str(path) + '.gz').is_file():
        return []
    from protocolize.private_protocol import Protocol
    return list(Protocol.load(path).tests or [])


def run_benchmark_suite(exe_path: Path, cfg: dict,
                        origin_dir: Path | None = None,
                        keep_going: bool = False,
                        timeout: int = 1800,
                        build_dir: Path | None = None) -> tuple[str, bool]:
    """Run the configured benchmark suite. Returns ``(output, ok)``.

    *origin_dir*, when given, is passed as the run's golden root so comparisons
    use the generated origin instead of the project's bundled goldens.

    ``timeout`` bounds the whole benchmark *process* (a hang guard).
    It must be generous: the runner runs the suite serially (a tight process
    timeout kills a legitimate full-suite run mid-way — audit finding 7.2.1).

    The command line comes from the plan — entries that do not exist yet
    simply have no benchmark, which is the honest answer.
    """
    del timeout, keep_going  # the plan's own case_timeout_s is the bound
    plan = load_plan(cfg or {})
    if plan is None:
        return '', True
    return _run_planned(plan, cfg, exe_path, origin_dir, build_dir)


def parse_total_cpu(bench_output: str, cfg: dict | None = None) -> float | None:
    """Load-immune CPU seconds from the benchmark output, if it reports any.

    The mechanism MEASURES its own aggregate (a ``RUSAGE_CHILDREN`` delta
    around each case) and prints ``Benchmark Total CPU time:``, so there is
    one spelling to read.  It used to delegate to a per-corpus
    ``bench_parse_cpu`` hook whose default then had to exclude two other
    spellings the corpora also print — a parsing problem that existed only
    because three scripts each printed their own.
    """
    del cfg
    for line in (bench_output or '').splitlines():
        if 'Benchmark Total CPU time' not in line:
            continue
        try:
            return float(line.split(':')[1].strip().rstrip('s'))
        except (IndexError, ValueError):
            return None
    return None


def extract_fail_details(bench_output: str, bench_ok: bool) -> str:
    """Collect benchmark failure lines into a '; '-joined summary string."""
    fail_lines = [line.strip() for line in bench_output.splitlines()
                  if line.strip().startswith(('CRASHED', 'TIMEOUT', 'SLOW', 'FAIL '))]
    fail_details = '; '.join(fail_lines) if fail_lines else ''
    if not fail_lines and not bench_ok:
        fail_details = 'DIFF (unknown)'
    return fail_details


# ── Origin (generated golden) management ──────────────────────
# Both delegate to the corpus's hooks for the origin's own shape.  What an
# "origin" IS differs by corpus: a golden-file corpus stores reference
# outputs, a self-verifying corpus (one whose drivers check their own
# residuals and print a verdict) has no golden to store and records that the
# reference build ran green instead.  That is the corpus's decision to make,
# so it lives in the corpus's hooks — never in a branch here.
def builder_forget_build(builder, build_dir: Path) -> None:
    """Discard what a BUILD derived, keeping what the corpus SHIPS.

    The origin tree holds both, and they must not be confused: the sources,
    the third-party libraries under it and its resources are the AUTHORITY
    for every later state (nothing else can produce them), while the objects,
    the CMake build directory and the products are derived and will be made
    again.

    Asked of the builder rather than hard-coded here, because which
    subdirectories are derived is a fact about the corpus's layout — MYSTRAN
    builds into `_build/`, a make corpus into `mis/`, `mds/` and `bd/` —
    and a guess that removes too little leaves a stale build while one that
    removes too much destroys the authority.
    """
    forget = getattr(builder, '_forget_build', None)
    if forget is None:
        return
    forget(Path(build_dir))


def _run_planned(plan, cfg: dict, exe_path: Path,
                 origin_dir: Path | None,
                 build_dir: Path | None) -> tuple[str, bool]:
    """Run the suite the plan describes.  Returns ``(output, ok)``."""
    build = Path(build_dir) if build_dir else Path(exe_path).parent.parent
    result = suite.run(
        plan, plan_env(plan, build, cfg, origin_dir, exe_path))
    return result.output, result.ok


def origin_ready(origin_dir: Path, cfg: dict | None = None) -> bool:
    """True if *origin_dir* already holds a usable origin for this corpus."""
    plan = load_plan(cfg or {})
    if plan is None:
        return False
    # COMPLETELY, not approximately: the old check was "any .out under here",
    # so a killed reference run leaving one file of 132 reported ready and
    # the cases without a golden were then scored as runtime-only passes.
    return suite.origin_ready(
        plan, plan_env(plan, Path(origin_dir), cfg or {}, Path(origin_dir)))


def save_origin(results_dir: Path, origin_dir: Path, cfg: dict | None = None,
                ok: bool = True) -> int:
    """Capture the origin from this run; returns the number of entries saved.

    A return of 0 means "nothing usable was captured" and the caller aborts —
    that is why a self-verifying corpus must record something explicit rather
    than vacuously copying nothing.
    """
    plan = load_plan(cfg or {})
    if plan is None:
        return 0
    if not ok:
        # A failed reference run captures NOTHING.  One rule, here, instead of
        # a guard each corpus's hook had to remember.
        return 0
    build = Path((cfg or {}).get('target_dir', Path(origin_dir).parent))
    env = plan_env(plan, build, cfg or {}, Path(origin_dir))
    env.results = Path(results_dir)
    return suite.ensure_reference(plan, env)


def _ensure_origin_from_plan(ctx, build_dir: Path, exe_path: Path,
                             origin_dir: Path) -> bool:
    """Capture the reference's own output, by running the plan ONCE.

    The plan's `ensure_reference` is both the run and the capture, so the
    verdict and the goldens come from the same execution.  A reference run
    that is not green captures nothing — by construction, not by a guard.
    """
    plan = load_plan(ctx.cfg or {})
    env = plan_env(plan, build_dir, ctx.cfg or {}, origin_dir, exe_path)
    _t0 = time.monotonic()
    n = suite.ensure_reference(plan, env)
    log.action(f"benchmark OK — {time.monotonic() - _t0:.3f}s (wall)",
               action=Action.OK)
    if n == 0:
        log.action("origin run captured nothing — aborting; see "
                   f"{env.results}", action=Action.ERROR)
        return False
    # Keep the origin EXECUTABLE with the baseline: the pure-Fortran binary
    # beside the goldens is what regression/perf tooling compares against.
    # Nothing to do when the build happened IN the origin — which is the
    # normal case, and where a copy would be the file copying onto itself
    # (`SameFileError`, measured).
    dst = origin_dir / 'bin' / exe_path.name
    if exe_path.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(exe_path), str(dst))
    log.action(f"origin ready — {n} goldens captured", action=Action.OK)
    return True


def origin_jobs(ctx) -> int:
    """Parallelism for the origin (pure-original) build: every logical core.

    Reading ``cfg['jobs'] or 1`` here instead pinned this build to **one**
    worker for every config that leaves the field null — the normal spelling,
    and what all three corpora ship — so a 6980-file LAPACK origin built one
    ``f951`` at a time on a 24-core box: ~6% CPU and tens of minutes.

    There is no longer anything to read: parallelism is a policy with no
    override, defined once as "every logical core".  ``ctx`` is kept as
    the parameter because every call site has one and this is the origin
    path's name for the answer.
    """
    return max(1, os.cpu_count() or 1)


def ensure_origin(ctx, mode: str = 'debug', timeout: int = 1800) -> bool:
    """Make sure ``$BUILD_DIR/origin_<mode>/`` exists before the EDD loop runs.

    Generated by building the ORIGINAL (un-replaced) project and running the
    full suite on it — NOT by using the project's bundled goldens.  Reused
    as-is when already present.  Returns True when ready.
    """
    origin_dir = Path(ctx.origin_dir)
    # The reference build's CMake dir IS the origin dir.  It used to be a
    # sibling scratch (`_origin_<mode>_tmp`) that the build wrote and the
    # benchmark then published from, and the split cost more than it bought:
    #   * the onboard skill captures a corpus's protocol from an ALREADY-BUILT
    #     tree, and the protocol's paths (a relative `-I`, a third-party
    #     header, a bundled library) point into THAT tree.  A flow that builds
    #     somewhere else leaves them pointing at a tree it no longer
    #     maintains — and the cure is not to rewrite the paths but for both
    #     to name the same directory;
    #   * a purged `build_<corpus>` can be rebuilt, and the rebuild lands
    #     where the protocol expects because there is only one place it could.
    # So there is ONE origin tree: the build happens IN it, and the reference
    # `.out` files stay in it too, exactly as before — the only thing that
    # changed is WHERE THE BUILD WROTE, which was the whole point.
    build_dir = origin_dir
    if origin_ready(origin_dir, ctx.cfg):
        log.action(f"origin {origin_dir} already exists — using directly",
                   action=Action.INFO)
        return True

    build_dir = Path(build_dir).resolve()
    log.action("origin missing — generating from original build ...",
               action=Action.INFO)
    builder = ctx.builder
    fflags, _cxxflags = builder._get_flags(mode)
    jobs = origin_jobs(ctx)
    # NOT `rmtree(build_dir)`: the build dir IS the origin tree, and the
    # origin tree is an AUTHORITY — it holds the corpus's sources, its
    # third-party libraries and its resources, which nothing can regenerate.
    # Only what a build DERIVES may be discarded, and the builder knows which
    # that is (see `builder_forget_build`): `_build/`, `bin/`, the objects.
    builder_forget_build(builder, build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    builder._setup_build_dir(build_dir, cpp_candidates_dir=None)
    ok, build_output = builder._build_fort(build_dir, jobs, fflags, clean=True)
    if not ok:
        log.action("origin build failed — cannot run EDD loop",
                   action=Action.ERROR)
        return False
    # Same two screen lines as build_and_bench: phase timing from the log
    # system's natural +seconds stamp, benchmark wall time explicit.
    log.action(f"build OK — {build_dir.name}",
               action=Action.OK)
    exe_path = build_dir / 'bin' / builder.BINARY_NAME
    # The origin dir is where this run's goldens are written, so it must exist
    # (as an empty dir) BEFORE the suite runs — the reference run fills it.
    origin_dir.mkdir(parents=True, exist_ok=True)
    # Run the FULL suite on the original.  No golden is compared; -k keeps
    # going so every test's .out is captured.
    #
    # WITH A PLAN the capture IS the run: `suite.ensure_reference` runs
    # the plan against the reference and keeps what the plan calls golden, so
    # calling `run_benchmark_suite` here as well ran the whole suite TWICE —
    # once for a verdict nothing consumes, once for the goldens.
    if load_plan(ctx.cfg or {}) is not None:
        return _ensure_origin_from_plan(ctx, build_dir, exe_path, origin_dir)
    _bench_t0 = time.monotonic()
    bench_output, bench_ok = run_benchmark_suite(
        exe_path, timeout=timeout, cfg=ctx.cfg,
        origin_dir=origin_dir, keep_going=True, build_dir=build_dir)
    _cpu = parse_total_cpu(bench_output, ctx.cfg)
    _ts = _cpu if _cpu is not None else time.monotonic() - _bench_t0
    log.action(f"benchmark OK — {_ts:.3f}s"
               + (" (cpu)" if _cpu is not None else " (wall)"),
               action=Action.OK)
    bc = count.BenchCounts.parse(bench_output)
    if bc.crash > 0 or bc.timeout > 0:
        log.action(f"origin run failed "
                   f"({bc.crash} crash, {bc.timeout} timeout) — cannot proceed",
                   action=Action.ERROR)
        return False
    n = save_origin(build_dir / RESULTS_DIR, origin_dir,
                    ctx.cfg, ok=bench_ok)
    if n == 0:
        log.action(f"origin run captured nothing "
                   f"(bench_ok={bench_ok}, {n} entries) — aborting; "
                   f"see {build_dir / RESULTS_DIR}",
                   action=Action.ERROR)
        return False
    # Keep the origin EXECUTABLE with the baseline: the _origin_*_tmp dir
    # is a scratch area rmtree'd on regeneration, but the pure-Fortran
    # reference binary is what regression/perf tooling compares against
    # (baseline timing, mixed-vs-pure syscall profiles).  Only a corpus
    # whose run target IS an executable has one to copy.
    exe_dst = None
    if exe_path.is_file():
        try:
            exe_dst = origin_dir / 'bin' / builder.BINARY_NAME
            exe_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(exe_path, exe_dst)
        except OSError as exc:
            log.action(f"origin exe copy failed: {exc}", action=Action.WARN)
    log.action(f"origin generated: {origin_dir} ({n} entries, "
               f"exe {exe_dst})", action=Action.INFO)
    return True
