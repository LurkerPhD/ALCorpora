---
name: onboard-project
description: 'Read a private protocol out of a project that already builds, and PROVE it by rebuilding. The protocol is what lets a flow build any state of that project without knowing anything about it. Trigger words: onboard project, 接入项目, onboard-project, 私有协议.'
argument-hint: '<build tree> [--out PATH]'
user-invocable: true
---

# Onboard a Project

Read a project's build as **data**, and prove the data by rebuilding from it.

You are producing one artifact: a **private protocol**. Everything downstream
reads that file and nothing else about the project — so the whole cost of
adding a project is producing this one file correctly, and the whole cost of
getting it wrong is paid later by every build that follows.

## What you are actually doing

**The build is the authority on the build.** Not your reading of its source
tree, not its documentation, not your expectations of how a CMake project
looks. Every project has some way to tell you what it would do without doing
it, and that answer is the ground truth:

| build system | ask it |
|---|---|
| ninja / CMake | `ninja -t compdb`, `-t commands <target>`, `-t targets all`, `-t query <target>` |
| GNU make | `make -Bn <target>` |
| meson | ninja underneath; `meson compile --dry-run` first |
| scons | `scons -n` |
| bazel | `bazel aquery` |
| autotools | it generates a Makefile: `make -Bn` |

**You are not limited to this table.** A project built by something else has
its own equivalent, and finding it is part of the job — the shape to look for
is "print the commands, run nothing". If you genuinely cannot find one, say
so and stop; do not infer compile commands from the sources, because a
guessed protocol is one whose flags are almost right, which is the most
expensive kind of wrong.

**Ask the build system's own sub-tools, never parse its files.** A manifest
is an internal format whose escapes, variable expansion and ordering rules
belong to that tool. `ninja -t compdb` and `make -Bn` are the interfaces the
tool maintains for exactly this question.

## The shape of the answer

`reference/protocol-template.json` is the annotated form, and a real
protocol from a real project is the best example. In brief:

```
{
  "protocol_version": 1,
  "compilation": [ {directory, command, file, output,
                    stem, language, provides, uses, preprocess}, ... ],
  "generated_inputs": [ {output, command, depends}, ... ],
  "products":         [ {output, kind, objects|members, libs, driver}, ... ]
}
```

The first four fields of a compilation entry are the standard
`compile_commands.json` fields, unchanged: a consumer that knows only that
format can read this file. Everything else answers questions a compile
database cannot, and each one is load-bearing:

**`stem`** — the unit's identity with the extension removed and case kept.
It is the protocol's key: a product names its members as stems, so the link
line refers to a unit by its identity rather than by the file that happens to
hold it.

**`provides` / `uses`** — the Fortran module graph. A unit converted to C++
emits no `.mod`, so this has to be known BEFORE anything compiles. Read it
from the sources; no build system records it.

**`preprocess`** — the preprocess step, when there is one. **CMake Fortran
compiles a scanned source in two steps and does not give them the same
flags: the `-D` set arrives on the preprocess edge only.** A consumer that
re-runs only `command` silently drops every conditional block.

**`products`** — what links, with **members recorded as stems**. This is what
makes a state-driven build possible at all: flipping a unit changes which
object the link line names without the product changing.

**`generated_inputs`** — sources the build MAKES. A replay that names one
without a rule to produce it looks for a file that is not there, and fails on
one missing file among thousands.

**`own_roots`** — which of those units are the CORPUS'S OWN, as opposed to a
library it vendored and links. A corpus is routinely fetched together with
its third-party dependencies (MYSTRAN ships `submodules/{lapack,BLAS,superlu,
metis,GKlib}` beside its own `Source/`), and those must be BUILT natively, not
converted: converting them rewrites a library the corpus links into thousands
of units of the corpus's own translation burden, and it is not academic —
measured, LAPACK's `dp` and MYSTRAN's own `dp` collided in a name-keyed
constant table and the corpus's `dlartg` came out single precision.

Record the corpus's own roots, and leave the field EMPTY when the project
vendors nothing (LAPACK, NASTRAN-95): empty means "no narrowing", so a corpus
that declares nothing keeps its whole protocol.

Ask the PROJECT, not the build tree — it states this itself, in one of two
ways, and neither needs you to know the project:

| ask | for |
|---|---|
| `git config -f <checkout>/.gitmodules --get-regexp '^submodule\..*\.path$'` | the directories the project declares it vendored |
| `<build>/CMakeCache.txt`, its `<Name>_SOURCE_DIR` entries | the sub-projects the top-level build actually pulled in (`CMAKE_HOME_DIRECTORY` is the project itself) |

The checkout is available because it is what you just built; the build tree
is the fallback for a run that captured a tree without its checkout beside
it. Prefer the checkout: `.gitmodules` is the project's own declaration,
while the cache is a build system's record of it.

Then close the set under generation: a unit made FROM a vendored unit is the
vendored library's too. That is not a detail — it is the case a path test
cannot see, and it cuts both ways. MYSTRAN's `${TREE}/Binaries/.../*_64.f`
are made from `submodules/lapack`, so they go; LAPACK's `*_64.f` are made
from LAPACK's OWN `BLAS/SRC`, so they stay. **Nothing about this rule
mentions a suffix**: the `_64` units are just units, and which side they
belong to is decided by what they were generated FROM. A unit that
participates in the build is part of the corpus unless the project says
otherwise.

Do NOT decide this from a directory scan, from "is it in an archive", or
from the exe/archive member sets — all three were tried on the real corpora
and all three are wrong. `liblapack.a`'s members are LAPACK's OWN code, so
"in an archive" marks the one corpus that IS a library as foreign.

**Two spellings of a path, and the difference decides whether the replay
works:**

* **under `${TREE}`** — a file the replay must MAKE for itself. It travels
  with the protocol.
* **absolute** — a file the replay READS. The project's own sources do not
  move.

A file being *inside the build tree* does not make it generated: NASTRAN-95's
`common_blocks.cpp` is hand-written and sits in the build tree because the
Makefile runs there. Spell it `${TREE}/...` and the replay dies on a file
nothing will ever create. **The test is "does the build produce it?", not
"where does it live?"**

`$` is reserved. Write a literal dollar as `$$`; a bare `$` is a malformed
placeholder and must be refused, not passed through — the consumer spells its
own variables the same way and will expand it to nothing.

## Procedure

### 1. Build the project with its own toolchain, and keep the tree

```bash
cmake -S <project> -B <project>/build -G Ninja && ninja -C <project>/build
# or: make -C <project>
```

An **unmodified** baseline is the ideal reference, because then every artifact
the replay produces can be compared against a real one.

### 2. Get the list of commands the build would run

Whatever the interface is (see the table above), you want every command:
compiles, links, archive steps, and any command that generates a source.

Ask for the **whole** build, not its default target — a default target may
build fewer products than the build has. For make that means `all`, and `-B`
is not optional: without it an up-to-date tree prints "Nothing to be done"
and you get an **empty answer with no error**, which reads exactly like a
project with no translation units.

**A Makefile at the root does not mean it is the whole build.** NASTRAN-95's
root Makefile compiles ONE file; 1,872 units are built by `mis/`, `mds/` and
`bd/`. Read the root *and* its subdirectories.

### 3. Interpret the commands

Group them by what they do. A compiler invocation with `-c` is a **compile**;
one without it is a **link**; `ar`/`ranlib` is an **archive** step.

For each compile, recover: the source it consumes, the object it produces,
and the directory it runs in. Then two things need care.

**A unit is not always one command.** CMake compiles a scanned Fortran source
in two edges — a `PREPROCESS` edge whose `file` is the real source and whose
`output` is a `<src>-pp.<ext>` intermediate, and a `COMPILER` edge that
consumes that intermediate. Looping over the records sees **twice as many
"sources" as there are units**, half of them files that exist only in the
build tree. Join the two on the intermediate, which is the one path both
edges agree on.

**A source can be compiled inside a link line**, with no object of its own
until the link runs. It is still a unit. Give it a synthesised compile — the
link line's own flags minus everything that names an input or an output —
because reusing the link line would carry every object of the product into
that unit's options as bare paths.

**Do not expand an archive into its members.** `ld` pulls only what it needs
out of an archive, so an unreferenced member stays out of the product;
listing every member as an input links them all. Measured: the expanded link
defined a symbol the project's own executable does not have.

### 4. Write the protocol, then PROVE it

**A protocol that has not been rebuilt from is not a product.** Generate a
build from the protocol ALONE, in a fresh tree, compile it, and compare
against the project's own:

* **inputs** — every unit the protocol describes must be one the project's
  build compiles, and vice versa;
* **generated sources** — every one must be produced, byte for byte;
* **products** — compare by **defined symbols**.

Symbols rather than bytes, deliberately. A Fortran object embeds its source
path AS SPELLED ON THE COMMAND LINE, so a make build (relative) and a CMake
build (absolute) can never produce identical objects however correct both
are; archive member order and timestamps differ for the same reason. **And
exclude versioned symbols** (`stdout@GLIBC_2.2.5`): whether an executable
re-exports one is a fact about the linker, not about the protocol. Measured
on MYSTRAN: with them, one product differed by exactly that symbol; without,
3,563 symbols and none differing.

**Compare products by NAME, not by the path the protocol records.** Where the
project's build puts its artifacts is that build's convention; a replay is
under no obligation to reproduce it.

**When the proof fails, fix the protocol — never the comparison.** A wrong
protocol is not a wrong answer; it is a wrong answer multiplied by every
build that follows, each failing somewhere that names the project rather than
the protocol.

### 5. What the proof is looking for

Four defects it has actually caught, each of which fails SILENTLY:

* **the module directory was replayed.** `-J<dir>` (gfortran) or
  `-module <dir>` (ifort) must be REMOVED from a unit's options, not carried
  through. A module directory is not a per-unit fact — the build system needs
  it to work out which unit must compile first, because a `.mod` is a
  compile-time dependency and nothing in a flat list of sources says so.
  Measured on LAPACK: `la_xisnan` and its `use la_constants` went unordered,
  and the replay failed on a module file not yet written.
* **a flag's arity was miscounted.** A flag that takes an operand and is
  counted as taking none leaves the operand behind as a bare token, and the
  compiler reads it as another input file. A flag WITH no operand counted as
  taking one swallows what follows. **Both spellings of a path-taking flag
  occur**: `-JSRC` and `-J SRC`.
* **the preprocess intermediate leaked into the options.** It is the file the
  capture build compiled INSTEAD of the source, so a string compare against
  the source cannot catch it.
* **output flags were kept.** `-o <path>` among a unit's options makes the
  compiler answer "output filename specified twice".

## The OTHER half: how the project says a run was CORRECT

A protocol answers "how is this built?".  The flow also asks "how do we know a
run of it was right?", and that is a **separate artifact**, `verification_plan.json`,
produced by the SAME onboarding pass and committed the same way (gzipped,
expanded on use).  It is one mechanism for every project, so getting it right
is the same kind of work as the protocol and getting it wrong costs the same.

**`reference/verification-plan-template.json` is the shape to fill in.**  It is
an annotated plan: every section carries a `_README` explaining what the field
means, what the project must declare for it, and what was MEASURED when it was
got wrong — read it before filling anything, because a field like `atol` is a
measurement only this project can make.  Stripping every `_`-prefixed key
leaves a plan `problems()` accepts, and a test pins that, so the template
cannot teach a shape the mechanism refuses.

**The deliverable is the filled-in JSON.**  Two ways to produce it, in this
order of preference:

1. **Extract it.**  `protocolize/onboard/extract.py` implements the reading of
   all three case shapes, the record layout, the comparison rules and the
   preparation facts — the call is in "Running it" below.  Extend it when the
   project declares something it does not yet read: an extractor that reads
   the project's own declaration is the whole point, and a value typed by hand
   is a value nobody can re-derive.
2. **Write it, then PROVE it.**  When the project's declaration has a shape the
   extractor cannot read, fill the template in directly — and then make the
   proof: `VerificationPlan.problems()` must return `[]`, and
   `python3 -m protocolize.verify.cli <build tree>` must give the same
   verdict the project's own benchmark gives.  A plan that merely loads is not
   a plan that judges.

Either way the facts that are NOT derivable from the tree — a restart chain,
the preparation a program needs in order to run — go in the project's own
facts file beside its other onboarding inputs
(`a facts declaration`), passed IN to the
capture, with `_comment` recording where each came from.

**Ask the project how it says a run is correct — do not infer it from the
outputs.**  Three declarations exist in practice, and they are told apart by
what the project HAS, never by its name:

| the project has | the plan says | who judges |
|---|---|---|
| tests its build system registered (`protocol.tests`) | `cases.kind: build_system_tests`, `target: tree`, `run.kind: suite` | the suite's own report (`self_report`) |
| a case list with an `INCLUDE` chain and per-record fields | `cases.kind: case_list` (+ the record layout, read from the file) | per-case, against the reference's own output |
| one directory per case | `cases.kind: one_per_dir` | per-case, against the reference's own output |

**What "the project's own declaration" means here.**  For the case list the
RECORD LAYOUT is the project's — which column is the deck, which is a
selector, which is a reference value and a tolerance — read THROUGH the
`INCLUDE` chain and from the **widest** record, because a corpus mixes
asserted and unasserted entries and a narrow one would drop the criteria for
the whole corpus.  For the volatile-line list and the numeric tolerance
(`rtol`/`atol`), pass the file that states them as `declaration=`; do NOT
search for it, because a search that widened until it found something would
find a NEIGHBOUR's.

**A declaration may live in a SIBLING.**  A validation suite maintained as its
own repository is a sibling directory named after the project
(`MYSTRAN` / `MYSTRAN_Validation`), so the search looks at the project and then
at the siblings whose names begin with it.  Ambiguity is REFUSED, never
resolved by picking one: picking silently verifies a corpus against another
project's cases, and the result reads as a wrong answer instead of a wrong
choice.

**Completeness is not optional, and `problems()` is the authority.**  A plan
is refused when it could produce a verdict with nothing behind it:

* no `summary.require_min` — a run whose results nobody could read would pass;
* a `process` run with no `inputs` — the workload is handed a bare input name
  with its cwd in a work dir, so a plan that does not say what to stage
  describes a run that reads nothing.  Measured: every case "passed";
* a `golden.source: none` with no `stamp` — then every origin counts as ready,
  including one a killed reference run left half written;
* a bare `$` in a path (`$$` is a literal dollar), or a `golden.root` that is a
  literal path rather than a binding;
* a `golden.root` under the capture tree (`${TREE}`) while
  `golden.source: reference_run` — that names a file the PROJECT SHIPPED, not
  one a run produced.  See "The golden is made, never borrowed" below.

**The golden is made, never borrowed.**  A repository supplies **input files**;
the tool runs the native build to make the golden.  That is what
`reference_run` means, and it is not a formality: a corpus that ships its own
reference output usually produced it on another machine, another compiler and
another operating system — one old corpus's shipped `.out` files all carry a
1995 banner from an OS this machine does not have, and not one of its 132 cases
is byte-identical to what the same decks produce here.  Using those as goldens
would turn a real comparison into a fixture nothing can satisfy, and worse, a
build that merely copied a stale file would "pass".

So the repository's own reference output is an **input**, and comparing against
it — a legitimate thing to want — belongs in the project's `declaration`, never
in `golden`.  `golden.root` is therefore restricted to a run output root
(`{origin}`, `{results}`); anything else is refused when the plan is read.

**The origin is one rule for every project**: run the REFERENCE instance
through this same plan and keep what the plan calls golden.  The reference and
the candidate therefore traverse the same code path, and "a failed reference
run captures nothing" is true by construction instead of remembered per
project.

```bash
python3 -c "
from pathlib import Path
from protocolize.onboard.extract import capture_verification
from protocolize.private_protocol import Protocol
from protocolize.verification_plan import VerificationPlan
proto = Protocol.load('<build tree>/protocol_fortran.json')
plan = VerificationPlan.from_json(capture_verification(
    proto, '<build tree>', '<build tree>', '<project root>',
    declaration='<the file that states its comparison rules>'))
print(plan.problems())          # [] is the only acceptable answer
plan.save(Path('<build tree>') / 'verification_plan.json')
"          # run from the deepf2c checkout root
```

## Available tools

`protocolize/onboard/` holds a working implementation of all of the above for
ninja and make, and `protocolize/onboard/selfcheck.py` holds the proof. Read
them as a reference for the method — and as something you may extend, replace
or ignore if the project in front of you needs a different approach. The
method above is the contract; those files are one way of meeting it.

```bash
python3 -c "
from protocolize.onboard.extract import extract_protocol
p = extract_protocol('<build tree>')     # runs the proof, refuses on failure
p.save('<tree>/protocol_fortran.json')
"          # run from the deepf2c checkout root
```

## What this skill does not do

It does not convert anything, run any pipeline, or build the project. It reads
one build and writes one file. Everything after that is the flow's.
