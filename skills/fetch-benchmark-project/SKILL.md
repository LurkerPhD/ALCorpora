---
name: fetch-benchmark-project
description: 'Fetch a LEGAL project into `benchmark/`, number it, and record its two protocols. A project is legal when it builds and verifies itself with its OWN toolchain AND both protocols self-verify (the build protocol replays the project; the verification protocol runs green on the project and on the replay). Trigger words: fetch benchmark, 接入 benchmark, benchmark 项目, 拉项目进 benchmark.'
argument-hint: '<github url or path> [--name NAME]'
user-invocable: true
---

# Fetch a benchmark project

`benchmark/` is the corpus this tool is developed against: each project is a
case that must pass **end to end**, and a project that fails is the next gap
to close. This skill admits one project: it numbers it, fetches the source,
proves it LEGAL, and records what the proof produced.

## What "legal" means

A project is **legal** for driving this tool's development when ALL of these
hold, in this order — a failure at any step means the project is not (yet)
legal, and the failure IS the finding:

1. **it builds natively** with its own toolchain (no build system we invent);
2. **it verifies natively** — its own tests/suite pass as the project runs
   them (this is the "自带验证" the goal requires);
3. **§A: the build protocol self-verifies** — a build generated from the
   protocol ALONE rebuilds the project and its products match;
4. **§B: the verification protocol self-verifies** — the plan runs green
   against the project's own build, AND against the protocol-driven rebuild
   (the flat build), reaching the SAME verdict.

A project that fails 3 or 4 is not rejected for being a bad project: it is
the next thing to develop. Record which step failed and why.

## Legacy is a fact, not a rejection

Some projects do not carry their own build system or their own verification
(measured: NASTRAN-95 ships sources and prebuilt binaries, no Makefile, no
case suite). They are admitted as **legacy**: the source is placed and the
number is assigned, but no protocol is recorded, and the reason is written
down. Do not invent a build system for them — a recorded command is the
authority, and inventing one is the thing this tool exists to avoid.

## Procedure

```
# 1. number it: the SMALLEST free three-digit number (a failed admission
#    leaves its number to be reused, not a hole)
# 2. fetch the SOURCE into benchmark/<NNN>-<name>/<name>/  (a real checkout)
# 3. build + verify NATIVELY, with the project's own toolchain
#    (CMake+Ninja, or the project's own as the skill researches it)
# 4. run the pipeline: extract_and_prove (§A), capture_verification (§B),
#    then run the plan against the project and against the replay
# 5. record the RESULT into benchmark/<NNN>-<name>/:
#       <name>/                the source, with its build tree
#       protocol.json          the build protocol  (a compile_commands.json)
#       verification_plan.json the verification protocol
#       prepare.sh             the project's one-time ops, when it needs any
```

The mechanical half is `scripts/fetch.py`; call it with the project's own
build commands. The skill's job is the part that is different per project:
finding those commands, and reading the failure when one of the four steps
above does not hold.

```bash
python3 skills/fetch-benchmark-project/scripts/fetch.py \
    <github url or local path> --name <name>
```

## When the native build fails: diagnose it, do not paper over it

Steps 1-2 fail on real projects, and the failure is INFORMATION. A project
written for another platform or an older compiler is a legal project whose
build needs a fix; finding that fix is YOUR job, not a script's — it differs
per project, which is exactly what a skill is for. The mechanical half only
runs what you decided and records it.

The loop:

1. **Read the failure.** It names its own cause. Real examples:
   * `No rule to make target '..\bd\dpdcbd.o'` — the Makefile spells target
     paths with BACKSLASHES (Windows). Confirm: the slashed form names a real
     file, the backslashed one does not. The fix is path normalisation.
   * `Error: Type mismatch between actual argument ...` / `BOZ constant ...
     nonstandard postfix syntax` — today's gfortran rejects a Fortran dialect
     the project was written in. The fix is the compiler flag gfortran
     documents for that diagnostic (`-fallow-argument-mismatch`,
     `-fallow-invalid-boz`, `-std=legacy`).
2. **Decide the fix, and make it idempotent.** Two kinds:
   * a **patch to the source** (a Makefile path, a build file) → a `prepare`
     op with a `check` (a shell line that succeeds when the fix is ALREADY
     there) and a `run` (the shell line that applies it);
   * a **build flag** → part of the `--build` command line the skill passes.
3. **Pass it in and re-prove.** `--prepare` takes the ops; the script records
   them into the protocol (they run at most once) and re-runs the whole
   proof. If the build still fails, the diagnosis was wrong — say so and stop;
   never record a fix that did not make the build work.
4. **Nothing is guessed.** A `check` that cannot be written, or a fix with no
   observable confirmation, is not a fix — it is a change to the project that
   the next reader cannot reproduce.

```bash
python3 skills/fetch-benchmark-project/scripts/fetch.py <url> --name X \
    --build "make -C source/mis FFLAGS='-std=legacy ...' && make -C source/mds all" \
    --prepare fix.json     # the ops YOU diagnosed, each with check + run
```

## The layout, and the one rule about it

```
benchmark/
  001-minFortran/   CMakeLists.txt source/          <- tracked SOURCE
  002-minCpp/       CMakeLists.txt src/             <- tracked SOURCE
  003-goldenDemo/   CMakeLists.txt src/ verification/  (golden)
  004-goldenChain/  ... + facts.json                (golden + chains)
  005-goldenField/  ... + facts.json                (golden + field model)
  006-cJSON/        cJSON/                           <- a whole checkout
  ...
  benchmark.csv     every project and how far it got
```

* **001-005 are the minimal SAMPLES**, one per verification kind, and their
  SOURCE is tracked: CMakeLists, sources, the verification INPUTS, and
  `facts.json` (what the project declares about its artifacts and chains).
  They are the reference shapes — a change that breaks one of them is a
  regression, and running them costs seconds.
* **006+ are real projects.** A whole third-party checkout is NOT tracked.
* **Generated protocols are OUTPUT, never tracked**: `protocol.json`,
  `verification_plan.json`, the build tree, and `origin_release/` are all
  built, not committed. The tool regenerates them.
* the number is the ADMISSION ORDER, three digits, and it never changes once
  a project is admitted. The NEXT project takes the smallest free number, so
  a failed admission does not leave a gap.
* a protocol records paths OUTSIDE the tree (the project's own sources) as
  ABSOLUTE, by design — so a project admitted here stays put. Moving its
  directory invalidates its recorded protocols; re-run with `--readmit`.
