# ALCorpora

> 中文版 / Chinese: [README.zh-CN.md](README.zh-CN.md)

Turn **any project** into two artifacts, each **self-verified at the exit**:

| artifact | answers | schema |
|---|---|---|
| **build protocol** | how is this project BUILT? | `private_protocol.Protocol` |
| **verification protocol** | how do we know a run of it was RIGHT? | `verification_plan.VerificationPlan` |

A protocol that has not been **rebuilt from** (build) / **run** (verification)
is not a product. Fortran is only the first *profile* — nothing in this repo
may name a language, a build system, or a project.

The build protocol is written as a `compile_commands.json` **array**: every
element before the last is a compile entry (the four standard fields plus this
protocol's extras), and the last is a node carrying the sections a compile
database cannot express. It can be handed to a cc-only consumer unchanged —
the array structure is never disturbed, and extra entries are permitted.

`PLAN.md` is the plan, the acceptance criteria and the measured evidence;
this file is how the thing actually runs. **Keep it current** — a diagram
here that names a module that moved misleads the next reader exactly as a
stale comment does.

---

## Non-negotiable invariants

1. **The build is the authority on the build.** Only ever ask the build
   system's own sub-tools (`ninja -t compdb/query/commands/targets`,
   `make -Bn`, `ctest --show-only=json-v1`). **Never parse a manifest** — a
   manifest is that tool's internal format.
2. **Two spellings of a path, opposite meanings.**
   * under `${TREE}` — the replay must **make** it (generated sources,
     products). It travels with the protocol.
   * **absolute** — the replay only **reads** it (project sources, third-party
     libs, configure-time headers). It does not travel and must not be
     rewritten.
   `$` is reserved: a literal dollar is `$$`, a bare `$` is refused.
3. **The stem is the unit's identity**: one trailing extension removed, case
   kept. A product names its members as stems, so the link line refers to a
   unit by its identity rather than by the file that happens to hold it. This
   repo does not convert anything — it reads a build and REPLAYS it, so a
   unit's language is just what it is, never a form to flip.
4. **The protocol CONTAINS a compile database; it is not one.** The file is a
   JSON object whose `compile_commands` element holds the entries, because a
   real cc loader is a strict whitelist and all-or-nothing: one entry carrying
   `stem`/`language`/`output` makes clangd reject the ENTIRE file. A valid cc
   is an export (`to_cc`, which drops every field cc does not define).
5. **Do not guess.** Ambiguity is *refused*, never silently resolved: many
   candidate case declarations (would verify against a neighbour's cases),
   stem collisions (would drop a unit), a make `$(shell)` source nothing
   produces, a state form the protocol does not carry.

---

## Module map

```
bench.py                THE TOOL ENTRY POINT: one atomic operation per
                        subcommand (tree/wipe/plan/verify/evidence/row/
                        fetch/admit); each delegates, none reimplements
admit.py                the admission flow (number, fetch, native, §A, §B, row)
alcorpora/
  tree_anchor.py        ${TREE} placeholder: expand / escape / validate (bare $ refused)
  compile_commands.py   read a compile database; a protocol CONTAINS one
  output_layout.py      output-directory rule; repo root; longest owning root
  private_protocol.py   build-protocol schema + problems() + canonical_stem
  verification_plan.py  verification-protocol schema + problems()
  flat_cmake.py         protocol + state -> one flat CMakeLists.txt
  volatiles.py          measure what two runs of one binary DISAGREE on
  onboard/
    extract.py          read a protocol OUT of a built tree (ninja/CMake);
                        capture_verification; own_roots; prepare; tests; support_files
    make_extract.py     the make backend (make -Bn)
    selfcheck.py        §A: rebuild from the protocol and compare
  verify/
    suite.py            run a verification plan; compute the verdict
    cli.py              CLI: python3 -m alcorpora.verify.cli <build tree>
    reference.py        origin (golden) capture; the plan's run-time environment
    compare.py          text/field comparison (lockstep / numclose / column model);
                        drop / keep / mask
    counts.py           BenchCounts: parse counts from a runner's own output
    log.py              logging / tee / Action
skills/onboard-project/  the skill: SKILL.md + scripts/onboard.py + reference/*.json + tests/
skills/fetch-benchmark-project/  admit ONE project into benchmark/: SKILL.md + scripts/fetch.py
```

**Layering.** `tree_anchor.py` is layer 1 (the transpiler needs it too); the
protocol code is layer 2 and imports it — so layer 1 never reaches back into
layer 2.

---

## The delivery flow

One admission, end to end. **`[PY]` is a step the tool performs
deterministically; `[SKILL]` is per-project knowledge the caller supplies; a
`SEAM` is where a step FAILS and the flow asks an agent — which it does
itself, adopting the answer only if running it makes the step pass.**

```python
ADMIT_ONE_PROJECT(source):
    # [SKILL] A1 — the per-project build commands: knowledge about THAT project
    A1  configure, build, test, tree_subdir = how_it_builds(source)

    # [PY] one call; the pipeline below is a SCRIPT except where a step fails
    #      and the flow opens a SEAM (below)
    P1  call:  python3 bench.py {fetch <source> | admit <dir>} <args...>
    P2  n, dest = smallest_free_number(), benchmark/{n:03d}-{name}
    P3  fetch(source -> dest/inner)              # clone/copy, retried; submodules
                                                 # checked and fetched with it
    P4  src = inner_source(dest)                 # by what it HOLDS, not its name
        clear_previous_build(src, tree_subdir)   # re-prove only; in-source keeps the dir

    # P5-P6 native: the project's own toolchain, its own verification
    P5  apply prepare_ops                        # idempotent {op, check, run}
    P6  run (configure, build, test)             # the project's OWN lines
        failed -> SEAM A

    # P7-P8 §A: the build protocol, PROVEN by replaying it
    P7  proto = extract_protocol(build)          # ask the build system's sub-tools
    P8  self_check(proto)                        # mirror; delete generated parts AND
                                                 # products; flat_cmake; compare symbols
        cannot read / does not replay -> TOOL GAP    # extend the extractor + a test

    # P9 the verification protocol, read from the project's OWN declaration
    P9  plan = capture_verification(proto, declaration, facts)
        not executable -> SEAM D

    # P10 the golden is MADE, never borrowed
    P10 goldens = ensure_reference(plan)         # reference_run only
        if 0 -> SEAM D

    # P11-P12 §B twice: the project's build AND the replay must AGREE
    P11 verify(project)      failed -> SEAM E
    P12 verify(replay)       failed -> TOOL GAP   # protocol and build disagree

    P13 record(PASS, language, notes)            # only here is it legal


# ══ A SEAM: the flow STOPS, asks an agent, adopts ONLY what a re-run proves ═══

SEAM(seam_id, what_failed):
    answer = ask_agent(seam_id, what_failed)     # a TYPED contract (seams.KINDS)
    if answer.kind == protocol_gap:
        report and STOP                          # the protocol must GROW; never retried
    adopt(answer); re-run the step that failed   # adopted ONLY if that step then passes
    the same failure is not asked twice in one run; passes are bounded
    no agent reachable -> the seam closes, the step fails as it would have
                         # an unreachable agent is NEVER a project's verdict

# the three seams, and what each will adopt:
#   A  P6 failed (build/verify)       -> native_commands | prepare_ops
#   D  P9/P10 (plan / no goldens)     -> facts | declaration
#   E  P11 failed (§B on the project) -> prepare_ops | facts | declaration
# any seam may instead answer protocol_gap: "the protocol cannot express this".
```

### What each step does

**`[PY]` — the pipeline (a script; a failure at P6/P9/P10/P11 opens a seam).**

* **P1** `bench.py {fetch|admit}` — the single entry point. `fetch` is a first
  admission (number, fetch, prove); `admit` re-proves one already in
  `benchmark/`. One command so there is one definition of the flow.
* **P2** number and destination — the smallest free number is the admission
  order, and the folder is `benchmark/<NNN>-<name>`.
* **P3** fetch the source — a shallow clone (retried, links are flaky) or a
  local copy. A failed copy's EMPTY destination is removed, or it would
  consume a number without being a project. **The submodules come with it**:
  fetching is not done until they are, so this step checks whether the tree
  vendors anything, runs the update only if it does, checks again, and
  retries until git reports them in place — a checkout that vendors nothing
  pays nothing. Doing it anywhere later would be the same command in a worse
  place, arriving as something to diagnose instead of as part of the checkout.
* **P4** find the tree, clear a previous build — the tree is whichever
  directory HOLDS a build system (never a name). An out-of-source build
  directory is removed whole; a build living among the sources has only the
  state the build system generated removed, never the sources.
* **P5** apply the prepare ops — the patches the agent diagnosed, each
  `{op, check, run}` and idempotent: `check` decides whether it is already
  applied, so a repeat costs nothing.
* **P6** the project's own build and verify — the per-project commands (A1).
  A failure opens SEAM A, and the printed log goes with it.
* **P7** extract the protocol — ASK the build system's own sub-tools
  (`ninja -t compdb`, `make -Bn`); never parse the manifest. Which backend is
  chosen by what the tree holds.
* **P8** PROVE it — mirror the tree, delete the generated sources AND the
  products (so the replay must MAKE them), generate a build from the protocol
  ALONE, compile, and compare each product by its DEFINED SYMBOLS. This is
  what makes the protocol a product rather than a description.
* **P9** read the verification protocol — from the project's OWN statement:
  which acceptance (registered tests | a case list | one directory per case),
  the comparison rules (`drop`/`keep`/`mask`/`rtol`/`atol`), and the facts its
  programs need. Refused if the plan would not be executable.
* **P10** make the golden — run the REFERENCE instance through the plan and
  keep what it produced. Made, never borrowed: a shipped reference output came
  from another machine and decade.
* **P11** §B on the project's own build.
* **P12** §B on the REPLAY — the two verdicts must agree. They are the same
  plan run against two builds, so a disagreement is the protocol's fault.
* **P13** record the row. Only here is the project legal.

**A SEAM — the flow stops, asks an agent, and adopts only what a re-run proves.**

A seam is a place where a deterministic step FAILS and no rule can decide what
to do. The flow opens it itself: it hands the agent the failure and the tree,
gets back a TYPED answer, and — this is the load-bearing part — **adopts it
only by running it.** An answer that does not make the failed step pass is
discarded by that step failing again, so nothing is taken on the agent's word.
The same failure is not asked twice in one run, and the passes are bounded.

* **SEAM A** (P6 failed) — the build or verify did not work. Accepts
  `native_commands` (the commands that DO build it) or `prepare_ops` (patches
  it needs first, each `{op, check, run}`, idempotent). **A fix that did not
  make the build work is never recorded.**
* **SEAM D** (P9/P10) — the captured plan is not executable, or the reference
  run captured nothing. Accepts `facts` (what the programs need in order to
  RUN that the tree cannot show — a home directory, a resource directory, a
  program config) or `declaration` (the project's OWN comparison rules:
  `drop`/`keep`/`mask`, `rtol`/`atol`).
* **SEAM E** (P11) — §B is not green on the project's own build. Accepts
  `prepare_ops`, `facts` or `declaration` — a project problem a patch can fix.

Any seam may instead answer **`protocol_gap`**: "the protocol cannot express
this". That is not retried and not guessed at — the flow records it and stops,
because growing the protocol is the tool's work, not the agent's.

**Not every stop is a seam.** §A cannot read the build, or the replay and the
project disagree — those are the TOOL's gaps, and the answer is to extend the
extractor or the comparator WITH A TEST, never to hand-write a protocol or a
plan to get past them. A seam that cannot reach its agent is the same: the
step fails exactly as it would have, and that is never a verdict about the
project.

**The one step no seam covers is `research_how_it_builds` (A1)** — a project's
native toolchain is knowledge about THAT project, and encoding a guess in the
tool would push project knowledge into the tool. It is supplied by the caller,
exactly like the project's own declarations.

---

## Build protocol: extract + prove (§A)

```
a built tree
   |
   |- build.ninja / CMakeCache.txt -> ninja backend
   |- Makefile (root or one level down) -> make backend
   |- neither                          -> REFUSE (do not guess)
   |
   v  Toolchain(build, root)
   |- targets()       -> (output, rule) for every edge
   |- compdb()        -> the compile database, cleaned by the build system
   |- commands_for()  -> per target; closure first, the target's OWN edge LAST
   |- inputs_many()   -> explicit vs everything behind | or ||
   |
   |- _raw_entries : COMPILER edges + PREPROCESS edges
   |     `- _edges : joined on the INTERMEDIATE
   |           (one unit != one compdb record: CMake compiles a scanned
   |            source in two edges)
   |           `- _units : stem = source below its own root, extension removed
   |
   |- _products    : link targets; their inputs recorded as STEMS; libs in ORDER
   |- _generated   : CUSTOM_COMMAND outputs + command + depends
   |- produced set : decides each path's SPELLING (must be known before spelling)
   |- own_roots    : which units are the project's own (.gitmodules / cache _SOURCE_DIR)
   |- tests        : ctest --show-only=json-v1
   `- support_files: files a test READS that are neither compiled nor generated
                     (carried as CONTENT)
   |
   v
Protocol  -- problems() must be [] -- (a list, not a raise: every gap at once)
   |
   v
_prove -> selfcheck.self_check():
   mirror tree -> delete the generated sources AND THE PRODUCTS
      (force the replay to MAKE them, not find them)
   -> flat_cmake -> cmake+ninja -> compare per product
      |- differs -> ExtractionError: REFUSE to hand over the protocol
      `- equal   -> return
```

The products are deleted for the same reason as the generated sources: a
build that leaves them beside its sources leaves them in the mirror too, so a
product whose command the replay never ran would still be *found* — in the
copy of the file it is compared against — and reported EQUAL while nothing
built it. Only spellings inside the replay's own tree are removed; an
absolute one names the capture tree, which the replay reads.

### §A comparison — the criteria

`selfcheck` compares **products**. `PLAN.md` §A fixes four checks per product
(the measured table below decides what may and may not go red):

1. **defined symbols** (`nm -g --defined-only`; drop archive-member headers
   and versioned symbols);
2. **normalized instruction sequence** (`objdump -d --no-show-raw-insn`):
   drop the address column and the `# <addr> <sym>` comment,
   `0x…(%rip)` -> `(%rip)`, `call/jmp 0x… <sym>` -> `call/jmp <sym>`;
3. **`.data` raw bytes** (`objcopy -j .data`) — catches `DATA` constants a
   code-only compare is blind to;
4. **section inventory** (`readelf -S` names+types; **not sizes**).

| construction | whole file | norm .text | .data | verdict |
|---|---|---|---|---|
| same code, path **length differs** | differ | **same** | same | EQUAL |
| `-O0` vs `-O2` | differ | **differ** | — | DIFFER |
| `__DATE__`/`__TIME__` a second apart | differ | **same** | same | EQUAL |
| `data x /1/` vs `/2/` | differ | same (blind) | **differ** | DIFFER |
| section base address shifted | same | same | same | EQUAL |

**Do not** use: whole-file sha256 (`__DATE__` counter-example), `.rodata`
contents (embedded source paths live there), raw `.text` bytes (a path-length
shift moves the rip disp), collecting `.o` files.

Mechanism: a path string lands in `.rodata`; `.text` refers to it by a
rip-relative disp, so a path-length change moves the disp (normalized away)
but not the instruction sequence. Products are PIE, so `.data` pointers are
load-time relocations — `.data` bytes are path-independent and safe raw.

> **State:** `selfcheck.py` today implements check (1) only. Checks (2)–(4)
> are M4.

---

## Verification protocol: capture + prove (§B)

```
capture_verification(proto, build, root, project_root, declaration, facts)
   |
   |- proto.tests non-empty?
   |     `- -> registry shape: target=tree, run=suite (ctest),
   |           golden=none + stamp, assertions=self_report (the project's own table)
   |
   `- else _locate(project, finders):
        |- case_list (cases.txt / testcases.txt / *.files)
        |     |- read records THROUGH the INCLUDE chain
        |     |- column layout from the WIDEST record (a list mixes
        |     |  asserted and unasserted entries)
        |     |- deck_root derived from the tree (not necessarily the list's dir)
        |     `- the report's suffix OBSERVED from the stored references, so a
        |        program that writes `.F06` for a `.bdf` deck is not assumed
        |        to write beside its input (a declaration still wins)
        |- one_per_dir (verification/ / Verification/ / tests/cases)
        |     |- input-name spelling derived from the tree
        |     `- the WHOLE directory is staged (support decks and restart
        |        state sit beside the input)
        `- none / ambiguous -> REFUSE
   |
   v
VerificationPlan.from_json -> problems() must be []
   (refuses: no require_min; a process run with no inputs; golden none with no
    stamp; a bare $; golden.root a literal path; golden.root outside a run
    output root under source=reference_run; a self-inconsistent chain)
   |
   v
run cli.main([tree]) on the reference -> exit code IS the verdict -> 0 proves it
```

**The origin is one rule for every project:** run the REFERENCE instance
through this same plan and keep whatever the plan calls golden. Reference and
candidate traverse one code path, so "a failed reference run captures
nothing" holds by construction.

**The golden is made, never borrowed.** A repository supplies *inputs*; the
golden comes from running the native build. `golden.root` is therefore
restricted to a run output root (`{origin}`, `{results}`) when
`golden.source: reference_run` — a root under the capture tree names a file
the project SHIPPED, which is agreement with another machine's compiler,
operating system and decade, not evidence this build ran correctly. Measured:
an old corpus ships a per-case `.out` for each of its 132 cases and not one is
byte-identical to what the same decks produce here.

A `golden.source: reference_run` proves the candidate **agrees with the
reference** — stability, not correctness. A project that ships its own
reference output has an external truth, but that output is an input, so the
comparison belongs in the project's `declaration`.

---

## Replay: protocol -> flat build

```
Protocol
   |
   v  the generated CMakeLists.txt runs the RECORDED commands, verbatim:
   |     a unit    -> its own `command`, in its own `directory`
   |                  (a two-step/scanned unit: its preprocess edge, then
   |                  its compile edge, which consumes the intermediate)
   |     a product -> the recorded link/archive `command`
   |     a generated source -> the recorded generator `command`
   |     the object path is read off the command (`directory` + `-o`)
   v
CMakeLists.txt -> cmake+ninja -> products -> §A comparison
```

**Verbatim, not reassembled.** The generator does not say "these sources,
these flags" and let CMake build a command: it replays the command the build
recorded. Reassembling drifts, and the drift is silent — measured on a real
project compiled with `-fvisibility=hidden`, letting CMake rebuild the link
lines produced `-rdynamic` and a LOCALISED `main` in every executable, so 19
products "differed" while their code was identical. Replaying the recorded
command removes the whole class: flags, `-fPIC`, visibility, link order and
source spelling are the build's own.

The generator still decides two things the recorded commands do not: the
`${TREE}` placeholder is expanded to the replay's tree, and a unit DEPENDS on
the object of every module it uses, so the compile order is CMake's.


## The benchmark

This tool is developed the way it is meant to be used: **pull a project that
verifies itself, run the whole flow on it, and when something breaks, fix the
tool.** `benchmark/` (gitignored) holds those projects; `benchmark.csv`
records each one's state.

A project is **legal** when it builds and verifies with its OWN toolchain,
its build protocol replays it (§A), and its verification protocol runs green
on the project AND on the replay (§B). The `fetch-benchmark-project` skill
admits one, numbers it by the smallest free number, and writes its row.

Current state is `benchmark/benchmark.csv`; the projects are read in
admission order, and a project that fails is the next gap to close — not a
project to drop.

## Running it

```bash
# package + skill unit tests
python3 -m pytest -q

# THE TOOL: one atomic operation per subcommand. Use these rather than
# improvising an equivalent — a second implementation drifts from the first
# in some detail that stays invisible until it bites.
python3 bench.py tree     <benchmark dir>   # which directory is the source tree
python3 bench.py wipe     <benchmark dir>   # remove THIS TOOL's artifacts (not the project's)
python3 bench.py plan     <benchmark dir>   # executable? and how many drop/keep/mask
python3 bench.py verify   <benchmark dir>   # §B verdict
python3 bench.py evidence <benchmark dir> --shift-date   # what two runs disagree on
python3 bench.py row      <benchmark dir>   # its line in benchmark.csv
python3 bench.py fetch    <url>  [--name … --build … ]   # FIRST admission
python3 bench.py admit    <benchmark dir> [--build … ]   # re-prove one

# §A end to end, on a real build
python3 -c "
from pathlib import Path
from alcorpora.onboard.extract import extract_and_prove
proto, report = extract_and_prove('<build tree>', tree_root='<root>',
                                  work_root='<scratch>')
print(report.ok, len(proto.compilation))
"                     # report.ok must be True
```
