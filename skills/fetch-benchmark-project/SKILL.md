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

## Procedure — one AUTONOMOUS loop

You run this to completion yourself. **Do not stop between iterations to ask
the user**: a project that fails at a step is the next thing to work, and the
loop is what "the skill handles it" means.

```
admit a project:
  repeat until the project is LEGAL, or you are genuinely BLOCKED:

    1. NUMBER it (the smallest free number) and FETCH the source.
    2. Build + verify NATIVELY, with the project's own toolchain.
       If it FAILS -> diagnose (below), turn the fix into a `prepare` op
                     and/or a build flag, and RETRY. Loop here until the
                     build succeeds or you cannot find a fix.
    3. §A: extract_and_prove — the protocol must replay the project.
       If it FAILS -> that is a TOOL gap: record it and work on the tool.
    4. §B: capture_verification, then run the plan on the project AND on
       the replay. If the flat verdict differs -> a TOOL gap.
    5. LEGAL -> record the row in benchmark.csv and move on.
```

"BLOCKED" means: the failure has no fix you can name AND confirm — a missing
toolchain that cannot be installed, a resource the repo does not ship. Report
that in one line. Everything else, fix and continue.

The mechanical half is `scripts/fetch.py`; the loop calls it, reads what it
says, and decides the next move. The part that is per project — the native
build commands, and the fix when it fails — is YOURS.

```bash
python3 skills/fetch-benchmark-project/scripts/fetch.py <url> --name X
#   succeeds            -> the project is LEGAL; move on
#   REFUSED / NOT LEGAL -> read the reason, decide the fix, retry:
python3 skills/fetch-benchmark-project/scripts/fetch.py <url> --name X \
    --build "<the project's build, with any flags you found>" \
    --prepare fix.json     # ops YOU diagnosed, each {op, check, run}
```

### Diagnosing a native build failure

The failure is INFORMATION, and it names its own cause. Read it, decide the
fix, make the fix reproduce, and retry — do not paper over it.

1. **Read the failure.** Real examples, and the shape of the fix:
   * `No rule to make target '..\bd\dpdcbd.o'` — a Makefile spelling target
     paths with BACKSLASHES (Windows). Confirm: the slashed path names a real
     file, the backslashed one does not. Fix: normalise the paths.
   * `Error: Type mismatch between actual argument ...` / `BOZ constant ...
     nonstandard postfix syntax` — today's gfortran rejects a Fortran dialect
     the project was written in. Fix: the flag gfortran documents for that
     diagnostic (`-fallow-argument-mismatch`, `-fallow-invalid-boz`,
     `-std=legacy`).
   * a missing tool (`fypp`, a specific compiler) — install it, or record it
     as a `prepare` op when it is a step the project itself needs.
2. **Make the fix idempotent, and reproducible.** Two kinds:
   * a **source patch** (a Makefile, a build file) -> a `prepare` op with
     `check` (a shell line that exits 0 when the fix is ALREADY there) and
     `run` (the line that applies it). It is then recorded in the protocol, so
     a fresh checkout reproduces the build and a repeat pays nothing.
   * a **build flag** -> part of the `--build` line you pass.
3. **Confirm by re-running the whole proof.** If the build still fails, the
   diagnosis was wrong: say so and try the next reading. **Never record a fix
   that did not make the build work.**
4. **Nothing is guessed.** A fix you cannot confirm, or a `check` you cannot
   write, is not a fix — it is a change to the project the next reader cannot
   reproduce.

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
