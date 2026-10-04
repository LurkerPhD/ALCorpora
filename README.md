# protocolize

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
protocolize/
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
    cli.py              CLI: python3 -m protocolize.verify.cli <build tree>
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

One admission, end to end. **`[AGENT]` is a judgement only generalisation can
supply; `[PY]` is a step the tool performs deterministically.** The agent is
entered ONLY where the tool STOPS, and each such place below states what it
must produce — and what it must never do.

```python
ADMIT_ONE_PROJECT(source):
    # [AGENT] A1 — the ONE step every project pays for generalisation on
    A1  configure, build, test, tree_subdir = research_how_it_builds(source)

    # [PY] one call; the whole pipeline below is deterministic
    P1  call:  python3 bench.py {fetch <source> | admit <dir>} <args...>
    P2  n, dest = smallest_free_number(), benchmark/{n:03d}-{name}
    P3  fetch(source -> dest/inner)              # clone/copy, with retries
    P4  src = inner_source(dest)                 # by what it HOLDS, not its name
        clear_previous_build(src, tree_subdir)   # re-prove only; in-source keeps the dir

    # P5-P6 native: the project's own toolchain, its own verification
    P5  apply prepare_ops                        # idempotent {op, check, run}
    P6  run (configure, build, test)             # the project's OWN lines
        on failure -> A6

    # P7-P8 §A: the build protocol, PROVEN by replaying it
    P7  proto = extract_protocol(build)          # ask the build system's sub-tools
        on refusal -> A7
    P8  self_check(proto)                        # mirror; delete generated parts AND
                                                 # products; flat_cmake; compare symbols
        on refusal -> A7

    # P9 the verification protocol, read from the project's OWN declaration
    P9  plan = capture_verification(proto, declaration, facts)
        on problems -> A9

    # P10 the golden is MADE, never borrowed
    P10 goldens = ensure_reference(plan)         # reference_run only
        if 0 -> A10

    # P11-P12 §B twice: the project's build AND the replay must AGREE
    P11 verify(project)                          # on failure -> A11
    P12 verify(replay)                           # on failure -> A12

    P13 record(PASS, language, notes)            # only here is it legal


# ══ [AGENT] entered ONLY where the tool STOPS; each produces DATA ═══════════

A6  JUDGE_BUILD_FAILURE(log):
        read(log); diagnose; the fix is one of:
            prepare_op {op, check, run}   # writes in the tree = a PATCH; idempotent
            build_flag                    # caller-level; not a patch
        retry P5                           # a fix that did not build is NOT recorded
        if none can be confirmed -> BLOCKED

A7  JUDGE_EXTRACTION_FAILURE(exc):
        "the protocol is wrong"           -> fix the arguments
        "the tool cannot read this build" -> extend the RULE in extract.py + a test
                                             # NEVER hand-write a protocol to get past it

A9  JUDGE_PLAN(problems):
        declaration wrong or missing      -> write the project's own declaration
        the capture cannot read a shape   -> extend capture_verification + a test
                                             # NEVER hand-write a plan to get past it

A10 JUDGE_NO_GOLDENS():
        write facts.json                   # what the programs need in order to RUN

A11 JUDGE_B_PROJECT(printed):
        P11a python3 bench.py evidence --shift-date     # [PY] two runs, second shifted
        P11b for each shape reported UNCOVERED: recognize it -> write a rule into the
             project's declaration  # drop | keep | mask; prefer mask; never drop a
                                    # line carrying a judged value
        P11c re-capture; re-measure (must be empty); re-verify on a FRESH capture
             # NEVER call a difference "reasonable" and move on

A12 JUDGE_B_FLAT(printed):
        green on the project but not on the replay -> the TOOL is wrong, not the project
```

### What each step does

**`[PY]` — the pipeline (deterministic; the agent is not consulted).**

* **P1** `bench.py {fetch|admit}` — the single entry point. `fetch` is a first
  admission (number, fetch, prove); `admit` re-proves one already in
  `benchmark/`. One command so there is one definition of the flow.
* **P2** number and destination — the smallest free number is the admission
  order, and the folder is `benchmark/<NNN>-<name>`.
* **P3** fetch the source — a shallow clone (retried, links are flaky) or a
  local copy. A failed copy's EMPTY destination is removed, or it would
  consume a number without being a project.
* **P4** find the tree, clear a previous build — the tree is whichever
  directory HOLDS a build system (never a name). An out-of-source build
  directory is removed whole; a build living among the sources has only the
  state the build system generated removed, never the sources.
* **P5** apply the prepare ops — the patches the agent diagnosed, each
  `{op, check, run}` and idempotent: `check` decides whether it is already
  applied, so a repeat costs nothing.
* **P6** the project's own build and verify — the per-project commands (A1).
  A failure STOPS here and the printed log goes to A6.
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

**`[AGENT]` — entered only where the tool STOPS, and each produces DATA.**

* **A6** a native build/verify failure — read the log, diagnose, and produce
  one of: a `prepare` op (writes in the project tree, so it IS a patch, and it
  must be idempotent with a check) or a build flag (caller-level, not a
  patch). Retry. **A fix that did not make the build work is never recorded.**
* **A7** §A refused — either the protocol/arguments are wrong, or the tool
  cannot read this build. The second is a TOOL gap: extend the rule, with a
  test. Hand-writing a protocol to get past it is forbidden.
* **A9** the plan is not executable — either the project's declaration is
  wrong or missing, or the capture cannot read a shape it declares. The second
  is a TOOL gap: extend `capture_verification`, with a test.
* **A10** the reference run captured nothing — the programs need something in
  order to RUN that the tree cannot show (a home directory, a resource
  directory, a program config). Declare it in `facts.json`.
* **A11** a golden comparison differs — MEASURE it (`bench.py evidence
  --shift-date`, two reference runs), recognize each moving line shape, and
  write a rule for it into the PROJECT's declaration (`drop`/`keep`/`mask`,
  preferring `mask`). Then re-capture, re-measure (must be empty), and re-verify
  on a FRESH capture. Never call a difference "reasonable" and move on.
* **A12** green on the project but not on the replay — the protocol and the
  build disagree, which is the TOOL's fault, not the project's.

The single `[AGENT]` step every project pays for is `research_how_it_builds`:
a project's native toolchain is knowledge about THAT project, and encoding a
guess in the tool would push project knowledge into the tool. Everything else
the agent does is triggered by the tool STOPPING, and produces data — a
prepared op, a fact, a rule, or an extended rule — never a step of the flow.

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
        |     `- deck_root derived from the tree (not necessarily the list's dir)
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
from protocolize.onboard.extract import extract_and_prove
proto, report = extract_and_prove('<build tree>', tree_root='<root>',
                                  work_root='<scratch>')
print(report.ok, len(proto.compilation))
"                     # report.ok must be True
```
