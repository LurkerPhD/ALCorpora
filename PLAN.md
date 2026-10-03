# PLAN — protocolize: any project → build protocol + verification protocol

## What this repo is

A **language-agnostic** tool that turns **any project** into two artifacts:

* a **build protocol** — a compile-database **superset** (units, products, generated
  inputs, prepare/infra ops, build flags);
* a **verification protocol** — how the project is RUN and judged (cases, inputs,
  golden, assertions, caveats).

Each protocol is **self-verified at the exit**: the build protocol by rebuilding the
project from it alone (§A); the verification protocol by running it on the project's
own build. A protocol that has not been rebuilt from / run is not a product.

Fortran is only the **first profile**. Nothing here may name a language, a build
system, or a project — that is the point of the repo boundary.

## Current state (done)

Extracted from `deepf2c` and committed (`b8001a6`), self-contained:

```
protocolize/
  tree_anchor.py        ${TREE}: the tree a replay must rebuild; $ escaping
  compile_commands.py   read a compile database (cc); TREE expansion
  output_layout.py      which roots the build's own sources live under
  private_protocol.py   the build-protocol dataclasses + problems()
  verification_plan.py  the verification-protocol dataclasses + problems()
  flat_cmake.py         protocol -> a flat CMake build
  onboard/extract.py    read a protocol OUT of a build (ninja/CMake, make)
  onboard/selfcheck.py  rebuild from the protocol and compare (the §A work)
  onboard/make_extract.py
  edd/bench_suite.py    run a verification plan; compute the verdict
  edd/bench_cli.py      CLI: run a plan against a build
  edd/bench.py          origin capture / reference
  edd/compare.py        text/field comparison
  edd/counts.py         BenchCounts (parsed from a runner's own output)
  edd/log.py
skills/onboard-corpus/  the skill: SKILL.md, scripts/onboard.py, reference/*.json, tests/
```

**Verified**: every module imports with only this repo on `sys.path`; no Python import
of the harness remains. Residual `deepf2c` strings are docstrings + a CMake project
name (step M2).

## Known f2c leftovers to remove (language-agnosticization)

These are **data/behaviour** couplings the import check cannot see:

1. `flat_cmake(runtime_include=...)` — **always** `include_directories(<f2c runtime>)` and
   links `3rd/openblas/libopenblas.so` when present (`_blas_so`). A pure rebuild needs
   none of it. → generalize to **`extra_includes: Sequence[str] = ()`**; the f2c runtime
   path is supplied by the caller. Keep the BLAS link as a caller-declared extra.
2. `private_protocol.CompileEntry.variant ∈ {'fortran','cpp'}` — an f2c flip concept.
   → rename to **`language`**; "the flip" = two entries of one `stem` in two languages.
3. `provides`/`uses` are **force-lowercased** (Fortran modules). → keep the fields,
   stop forcing case here; a language profile decides its own spelling.
4. `build.flags.optimize` (source→converted rewrite) and `build.module_dirs` (`.mod`)
   are f2c/toolchain specific. → keep the fields **optional**, empty for a general
   project; do not branch on them in generic code.
5. `flat_cmake` writes `project(f2c_state ...)`. → a neutral project name.

## The delivery flow (the tool's job, per project)

| step | what | who |
|---|---|---|
| 1 | research the project: build system, how it is verified, whether it needs **prepare** (patch / fetch) | skill (agent) |
| 2 | **build natively once → verify natively once**. If that fails: a fix that persists → a `prepare` op; otherwise **report failure** | skill (agent) |
| 3 | unify into **build protocol + verification protocol** (fill the templates) | `extract.py` / `onboard.py` |
| 4 | **build-protocol self-verify**: protocol → flat build → **binary-identical** to native (§A); differ → fix the protocol, re-verify | `selfcheck.py` |
| 5 | **verification-protocol self-verify**: the protocol-driven flat build runs the flat verification → behaves as native; every correctness-irrelevant caveat met on the way is written **into the plan** | `bench_suite` / `onboard.py` |

Why 1–2 are the skill's and not a script's: "how to build project X" is a **research
result that differs per project** (a hand-written shell here, CMake there). Encoding it
in the tool would push project knowledge into the tool. The skill researches and **runs
it once**; `onboard.py` stays "capture from the built tree + two self-verifications".
A reusable native-build command is recorded as a `prepare` op (idempotent, with `check`).

## §A build-protocol self-verify — the comparison

Today `selfcheck.py` compares products by **defined symbols only** (blind to code).
Replace with **four checks per product** (executable / archive):

1. **defined symbols** (`nm -g --defined-only`; keep the archive-member-header and
   versioned-symbol filters already in `_defined_symbols`);
2. **normalized instruction sequence** (`objdump -d --no-show-raw-insn`): drop the
   address column, drop the `# <addr> <sym>` comment, `0x…(%rip)`→`(%rip)`,
   `call/jmp 0x… <sym>`→`call/jmp <sym>`;
3. **`.data` raw bytes** (`objcopy -j .data`) — catches `DATA` constants, which a
   code-only compare misses;
4. **section inventory** (`readelf -S` names+types; **not sizes**).

**Do not**: whole-file sha256 (`__DATE__` counter-example), `.rodata` contents (where
embedded source paths live), raw `.text` bytes (a path LENGTH shift moves the rip
disp), collecting `.o` files (more complex, and unnecessary — see below).

**Why product-level suffices** (measured):

| construction | whole file | norm .text | .data | verdict |
|---|---|---|---|---|
| same code, path **length differs** | differ | **same** | same | EQUAL |
| `-O0` vs `-O2` | differ | **differ** | — | DIFFER |
| `__DATE__`/`__TIME__` a second apart | differ | **same** | same | EQUAL |
| `data x /1/` vs `/2/` (prints 1/2) | differ | same (blind) | **differ** | DIFFER |
| section base address shifted | same | same | same | EQUAL |

Mechanism: a path string lands in `.rodata`; `.text` refers to it by a rip-relative
disp ⇒ a path LENGTH change moves the disp (normalized away) but not the instruction
sequence. Products are **PIE**, so `.data` pointers are load-time relocations ⇒ `.data`
bytes are path-independent and safe to compare raw.

Tools `objdump`/`readelf` are probed with `shutil.which`; missing → `SelfCheckError`,
like the existing cmake/ninja probe.

## §B verification-protocol self-verify + caveats

* The **golden must be freshly produced on the machine that runs it**, not a stale
  file. `verification_plan` expresses this with `golden.source ∈ {reference_run, none}`:
  a project with a reference → `reference_run` (run it here); a self-checking suite →
  `none` + `stamp`.
* The **behaviour union** is already expressible by orthogonal fields (no per-project
  schema): reference+text_lockstep+case_dir / reference+field_assertion+input_only /
  none+self_report+suite. "No golden" and "exit status matters" already work.
* **Caveats** (correctness-irrelevant lines that would otherwise fail a run — timestamps,
  wall-clock, volatile rows) must be captured **into the plan**. Landing spots:
  `assertions[*].drop` (regexes), `rtol`/`atol`, and possibly a structured
  `caveats: [{kind, pattern, why}]`; prose in `provenance`/`comment`.
  **Shape TBD** — decide while onboarding the first real project.
* **Self-verify**: `onboard.py` already runs the plan on the reference
  (`bench_cli` exit code = verdict). Add: the **protocol-driven flat build** runs the
  same plan and reaches the same verdict.
* Runtime resources (a `dmap/`-style data dir) → **`prepare`** (install once);
  validation **inputs** (a `Build_Test_Cases/` tree) → **`support_files`** (content).

## Independence (no external declarations)

A legal project is **self-contained**: everything the build/verification needs is
**obtainable from the project**. Therefore:

* **no `facts_<project>.json`** — every fact is derived from the project. `drop` is
  derived by **running the reference twice and diffing** (volatile lines); artifact
  suffixes by observing what the run writes; suite width / section patterns / total
  labels from the project's own test config; prep from the project's own config.
  What genuinely cannot be derived (e.g. an editorial restart-chain order) becomes a
  **declaration requirement of a legal project**, never a silent external file.
* The build declarations a recorded command cannot carry (policy flags, the
  converted-level map) come from the **skill's own declaration**, not from the
  consumer's config — the protocol is the authority, the consumer's config is
  generated **from** it.

## Acceptance

1. **Self-contained**: no Python import of the harness; the suite runs here alone.
2. **Any project**: given a project's sources + its native build/verify, produce two
   protocols, both self-verified.
3. **Build protocol correct** (§A): symbols + normalized instructions + `.data` +
   section set match native; `-O0/-O2` differences are **caught** (negative control);
   `__DATE__` / path-length differences do **not** false-red.
4. **Verification protocol correct** (§B): the plan on the reference = PASS; the flat
   build runs the same plan with the same verdict; caveats met along the way are in
   the plan.
5. **No project names / no facts files** in this repo.
6. **First profile is Fortran**: the three Fortran corpora still yield a unified
   protocol through this tool (checked from deepf2c's side, later).

## Milestones

* **M1 (nearly done)** — extracted & self-contained (import check green). **Finish**:
  eliminate the `runtime_include`/`variant`/`optimize` data couplings listed above.
* **M2** — generalization pass (language-neutral schema; Fortran is a profile).
* **M3 (done)** — this repo, `git init`, initial commit.
* **M4** — §A comparison upgrade + §B caveat channel + independence (no facts).
* **M5** — onboard a **second, non-Fortran** project to prove "any project" (the tool
  is developed the same way the harness is: a real project exposes the next gap).

## Key files (this repo)

* `protocolize/onboard/selfcheck.py` — the §A comparison
* `protocolize/onboard/extract.py` — capture (facts elimination lands here)
* `protocolize/private_protocol.py`, `protocolize/verification_plan.py` — schema
* `protocolize/flat_cmake.py` — runtime_include → extra_includes
* `protocolize/edd/{bench_suite,bench_cli,bench}.py` — run + verdict
* `skills/onboard-corpus/` — the skill (research + research-and-run; fills the templates)
