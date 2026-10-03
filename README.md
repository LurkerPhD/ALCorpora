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
4. **Do not guess.** Ambiguity is *refused*, never silently resolved: many
   candidate case declarations (would verify against a neighbour's cases),
   stem collisions (would drop a unit), a make `$(shell)` source nothing
   produces, a state form the protocol does not carry.

---

## Module map

```
protocolize/
  tree_anchor.py        ${TREE} placeholder: expand / escape / validate (bare $ refused)
  compile_commands.py   read a compile database (layer 1; a protocol is its superset)
  output_layout.py      output-directory rule; repo root; longest owning root
  private_protocol.py   build-protocol schema + problems() + canonical_stem
  verification_plan.py  verification-protocol schema + problems()
  flat_cmake.py         protocol + state -> one flat CMakeLists.txt
  onboard/
    extract.py          read a protocol OUT of a built tree (ninja/CMake);
                        capture_verification; own_roots; prepare; tests; support_files
    make_extract.py     the make backend (make -Bn)
    selfcheck.py        §A: rebuild from the protocol and compare
  verify/
    suite.py            run a verification plan; compute the verdict
    cli.py              CLI: python3 -m protocolize.verify.cli <build tree>
    reference.py        origin (golden) capture; the plan's run-time environment
    compare.py          text/field comparison (lockstep / numclose / column model)
    counts.py           BenchCounts: parse counts from a runner's own output
    log.py              logging / tee / Action
skills/onboard-project/  the skill: SKILL.md + scripts/onboard.py + reference/*.json + tests/
```

**Layering.** `tree_anchor.py` is layer 1 (the transpiler needs it too); the
protocol code is layer 2 and imports it — so layer 1 never reaches back into
layer 2.

---

## The delivery flow

```
                     any project (language-agnostic)
                          |
   1  research: which build system? how is it verified? does it need prepare?
                          |                    <- this step is the SKILL's, not a script's:
   2  build natively once + verify natively once    "how to build X" is a per-project
        |- fails, and a fix persists -> record a prepare op    research result
        |- otherwise                 -> report failure
                          |
   3  unify: extract.py / onboard.py fill the two templates
                          |
              +-----------+-----------+
              v                       v
        [build protocol]        [verification protocol]
              |                       |
   4 §A self-verify:          5 §B self-verify:
      protocol -> flat build ->    the same plan runs on the reference
      compile -> compare native    + the flat build reaches the same verdict
              |                       |
              +-----------+-----------+
                          v
             two self-verified artifacts
```

Steps 1–2 belong to the skill because "how to build project X" differs per
project (a hand-written shell here, CMake there). Encoding it in the tool
would push project knowledge into the tool. A reusable native-build command
is recorded as a `prepare` op — idempotent, with a `check`, so it runs **at
most once**.

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
   |- _products    : link targets; members/objects recorded as STEMS; libs in ORDER
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
    stamp; a bare $; golden.root a literal path; a self-inconsistent chain)
   |
   v
run cli.main([tree]) on the reference -> exit code IS the verdict -> 0 proves it
```

**The origin is one rule for every project:** run the REFERENCE instance
through this same plan and keep whatever the plan calls golden. Reference and
candidate traverse one code path, so "a failed reference run captures
nothing" holds by construction.

A `golden.source: reference_run` proves the candidate **agrees with the
reference** — stability, not correctness. A project that ships its own
reference output has an external truth: say so in `provenance`.

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

# §A end to end, on a real build
python3 -c "
from pathlib import Path
from protocolize.onboard.extract import extract_and_prove
proto, report = extract_and_prove('<build tree>', tree_root='<root>',
                                  work_root='<scratch>')
print(report.ok, len(proto.compilation))
"                     # report.ok must be True

# §B: run a plan against a build
python3 -m protocolize.verify.cli <build tree>          # exit code = verdict
```
