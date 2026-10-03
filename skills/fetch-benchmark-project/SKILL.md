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

## The layout, and the one rule about it

```
benchmark/
  001-minFortran/     CMakeLists.txt source/ protocol.json verification_plan.json
  002-minCpp/         CMakeLists.txt src/     protocol.json verification_plan.json
  003-cJSON/          cJSON/                   protocol.json verification_plan.json
  ...
```

* `benchmark/` is **gitignored** — it holds whole third-party checkouts, and
  the admission order is local state, not a fact about this tool.
* the number is the ADMISSION ORDER, three digits, and it never changes once
  a project is admitted: the projects are read in the order the failures were
  found. The NEXT project takes the smallest free number, so a failed
  admission does not leave a gap.
* a protocol records paths OUTSIDE the tree (the project's own sources) as
  ABSOLUTE, by design — so a project admitted here stays put. Moving its
  directory invalidates its recorded protocols; re-run the skill instead.
