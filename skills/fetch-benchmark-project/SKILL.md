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

## Procedure — ONE call; the flow repairs itself at a seam

```bash
python3 bench.py fetch <url-or-path> --name X      # a FIRST admission
python3 bench.py admit <benchmark dir> --…          # re-prove one
```

**The loop itself is the tool's.** Numbering, fetching, the native build and
test, §A, §B on the project and on the replay, and the row — `admit.py` runs
them in order. When a step FAILS the flow opens a SEAM and asks an agent
ITSELF, adopting the answer only if re-running that step passes — so you do
not re-run those steps by hand, and you do not hand-diagnose a failure the
seam already takes. You read what the call returns and supply the ONE thing no
seam can: **the per-project build commands — how THIS project builds.**

You run this to completion yourself. **Do not stop between iterations to ask
the user**: a project that fails at a step is the next thing to work, and the
loop is what "the skill handles it" means.

```bash
# the answer names the step that failed; give the project its own commands
python3 bench.py fetch <url-or-path> --name X \
    --configure "" \                                  # nothing to configure
    --build "make -C source/mis && make -C source/mds" \  # its OWN build
    --test "<a script that runs it and checks its own banner>" \
    --tree-subdir source
```

You MAY also pass `--prepare` / `--facts` / `--declaration` when you already
know them, but the flow's seams produce those itself when you do not.

**What the seams do without you.** A native build or verify that fails opens
SEAM A; a plan that will not run, or a reference run that captures nothing,
opens SEAM D; §B failing on the project's own build opens SEAM E. Each may
instead answer `protocol_gap` — "the protocol cannot express this" — and stop,
because growing the protocol is the tool's work, not the agent's.

A `BLOCKED` / `PROTOCOL_GAP` / `REFUSED` you get back means the seam did NOT
resolve it. Report that in one line.

**A refusal that is not about the project is a TOOL gap.** When §A cannot read
the build, or the capture cannot read a declaration shape, the fix is to
extend the rule that is wrong — `onboard-project` names each one and where it
lives — **never** to work around it in this loop with a hand-made protocol or
plan. That is the difference between the corpus teaching the tool something
and the corpus being special-cased, and the second is invisible afterwards.

**Use `bench.py`, not a hand-written equivalent.** Every atomic operation has
one definition there (`tree`, `wipe`, `plan`, `verify`, `evidence`, `row`,
`fetch`, `admit`), and an improvised replacement drifts from it in some detail
that is invisible until it bites — measured twice: a re-derived "which
directory is the tree" answered wrongly for a checkout with no build-system
marker at the top, and a re-derived "clear the build" deleted a make project's
source directory. When an operation is missing, ASK for it to be added to
`bench.py`; do not write a second one beside it.

**The defaults are CMake+Ninja and they are ONLY defaults.** `--configure`,
`--build`, `--test` and `--tree-subdir` describe ONE build system, so a project
that is not CMake must say so or it is refused at `cmake -S ...` — a failure
that reads as "this project does not build" when the truth is that nobody told
it how. `--configure ""` is the right answer when there is nothing to
configure: "nothing to do" is true about such a project, while running CMake
at it is not.

**If it is NOT CMake/ninja, put a compile database in the `--configure`/
`--build` you pass.** For such a project §A falls back to `make -Bn`, which
GUESSES what make would do and has a silent blind spot: `$(shell …)` does not
expand under `-n`, so sources a Makefile generates through a shell command
appear as no rule at all. An exported `compile_commands.json` is a static
record of what actually ran. So ask the build for one — e.g.
`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` for a Makefiles-generator CMake build, or
`bear -- make …` / `compiledb` for a plain make one. This is part of the
per-project commands you are already choosing; it is not a new tool.

Two limits, both real. It is a gain ONLY where the fallback is `make -Bn` — a
`build.ninja` tree already answers through `ninja -t compdb`, which reads the
current build graph, so for those there is nothing to add. And the tool reads
a database it finds PREFERENTIALLY, so it must be exported by THIS build: a
stale one from a different configure or a different flag set would outrank the
build graph that is actually here.

**Commands that reference the source.** `{src}` in any of these is replaced
with the project's source directory, so a path can be written relative to it
rather than absolute. When a project needs a real verification driver that its
source tree does not contain, write one (a small script beside the project's
other onboarding inputs) and point `--test` at it — that script is a
per-project research result, exactly like the build commands.

### What SEAM A does about a native build failure

**You do not do this by hand.** A native build or verify that fails opens SEAM
A, which hands the agent the failure and the tree and adopts only an answer
that makes the step pass — so a fix that did not make the build work is never
recorded. The knowledge it carries:

* `No rule to make target '..\bd\dpdcbd.o'` — a Makefile spelling target paths
  with BACKSLASHES (Windows). The slashed path names a real file, the
  backslashed one does not.
* `Error: Type mismatch between actual argument ...` / `BOZ constant ...
  nonstandard postfix syntax` — today's gfortran rejects a Fortran dialect the
  project was written in; gfortran documents a flag to allow it
  (`-fallow-argument-mismatch`, `-fallow-invalid-boz`, `-std=legacy`).
* a missing tool the build calls.

An answer is either `native_commands` (the commands that DO build it) or
`prepare_ops` (patches it needs first — each `{op, check, run}`, idempotent; a
build script is part of the source, so editing one IS a patch). A fix you
cannot confirm is not a fix.

**Your part stays one step earlier: the commands in the first place.** If the
project is not CMake, say so on the command line, or the flow is refused at
`cmake -S ...` — a failure that reads as "this project does not build" when the
truth is that nobody told it how.

### When the cases run but every one reports nothing

A program that needs a home directory, a resource directory or a *program
config* before it will run does not fail — it exits 0 and writes nothing, and
that reads as a broken project instead of a missing declaration. The names
(home variable, config file, the text inside it, the resource directory) are
facts about THAT project and nothing in the tree can show them, so they are
**declared**. The flow's SEAM D produces exactly this when the plan will not
run or the reference run captures nothing — you pass `--facts` (and
`--declaration`, below) only when you already know them. The capture refuses a
declaration that names a resource the tree does not have, rather than skipping
it, because a run that cannot start must never report a difference.

`--declaration` is the other half: the file where the project states its
COMPARISON rules (which lines are volatile, how much numeric slack its physics
allows). Pass it in if you have it; never search for it, because a search that
widened until it found something would find a neighbour's.

Both are per-project research results, exactly like the native build commands.
Relative paths resolve against the project source, so a facts file kept beside
the source travels with it.

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
