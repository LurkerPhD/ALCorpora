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

**The build is the authority on the build**, and the TOOL asks it — `bench.py
extract` runs the build system's own sub-tools and reads their answer. You do
not run them yourself, and you do not read the project's source tree,
documentation, or manifest to decide what the commands were. A manifest is an
internal format whose escapes, variable expansion and ordering rules belong to
that tool; the sub-tool is the interface maintained for exactly this question.

The table below is therefore **not a list of commands for you to run**. It is
the knowledge to extend the tool when a project is built by something the tool
has no backend for — the shape to look for is "print the commands, run
nothing":

| build system | the interface |
|---|---|
| ninja / CMake | `ninja -t compdb`, `-t commands <target>`, `-t targets all`, `-t query <target>` |
| GNU make | `make -Bn <target>` |
| meson | ninja underneath; `meson compile --dry-run` first |
| scons | `scons -n` |
| bazel | `bazel aquery` |
| autotools | it generates a Makefile: `make -Bn` |

A project built by something else has its own equivalent, and finding it is
part of the job. If you genuinely cannot find one, say so and stop: **do not
infer compile commands from the sources.** A guessed protocol is one whose
flags are almost right, which is the most expensive kind of wrong — and the
proof in step 2 exists to catch exactly that, so a guess will be refused
rather than silently accepted.

## The shape of the answer

`reference/protocol-template.json` is the annotated form: **every field carries
a `_README`** saying what it means, what the project must declare for it, and
what was MEASURED when it was got wrong. Read the template, not a copy of the
template. `Protocol.problems()` is the authority on whether a protocol is
well-formed.

**The file is a JSON object, and `compile_commands` is one element of it — a
compile database is CONTAINED, not the file.** Each entry carries the standard
cc fields plus the ones a database cannot hold, so the stored entries are
deliberately NOT valid cc: a real loader is a strict whitelist and rejects the
ENTIRE file over one unknown key. A valid compile database is an EXPORT
(`Protocol.to_cc`). Pinned by a test, so the two spellings cannot drift.

**The rules that decide the entries — which units, which products, which are
the project's own, how a path is spelled — are implemented, and each one's
docstring carries its reasoning and the measurements behind it.** They live in
`protocolize/onboard/extract.py` (`_units`, `_products`, `_generated`,
`own_roots_for`) and `protocolize/tree_anchor.py` (the `${TREE}` spelling).
Read them there when a project needs a rule extended, and read the refusal
`bench.py extract` prints when one is violated — that refusal names the rule.

**Two things about the output are worth knowing before you run anything**,
because neither is discoverable from the file alone:

* **`stem` is the unit's identity** — the source below its own tree root with
  one extension removed, case kept. A product names its inputs as stems, so a
  link line refers to a unit by identity rather than by the file holding it.
* **a path has two spellings and they mean opposite things:** under `${TREE}`
  the replay must MAKE it (it travels with the protocol); absolute, the replay
  only READS it (it does not travel). What decides is **"does the build
  produce it?"**, never where the file happens to sit. A bare `$` is refused.

## Procedure

**Every step that has a command IS that command.** The reading of the build —
which commands ran, which are units, which are products, which units are the
project's own, how every path is spelled — is `bench.py extract`, which also
PROVES what it read. Do not reproduce any of it by hand: a hand reading
differs from the tool in some detail that is invisible until the replay fails,
and then it is the tool that is right.

### 1. Build the project with its own toolchain, and keep the tree

This is the one step that is YOURS — the commands differ per project, and
finding them is the research `fetch-benchmark-project` describes. Build
**unmodified**: every artifact the replay produces is then comparable against
a real one.

```bash
cmake -S <project> -B <project>/build -G Ninja && ninja -C <project>/build
# or: make -C <project>
```

### 2. Extract the protocol, and let it PROVE itself

```bash
python3 bench.py extract <build tree> --out <protocol.json>
```

That is the whole of §A. It reads the protocol out of the built tree, then
rebuilds from the protocol ALONE in a fresh tree and compares — by **defined
symbols**, excluding versioned ones, because a Fortran object embeds its
source path as spelled on the command line, so two correct builds of the same
code are never byte-identical.

**A non-zero exit is the next thing to work on, and it names the rule that
failed.** Two outcomes, and they are different:

* **the protocol is wrong** — fix it, and this is almost always the case. A
  wrong protocol is not a wrong answer; it is a wrong answer multiplied by
  every build that follows.
* **the extractor cannot read this build** — a TOOL gap. The rules it applies
  are documented where they live, each with the measurements behind it:
  `protocolize/onboard/extract.py` (`units`/`_units`, `_products`,
  `_generated`, `own_roots_for`, `capture_prepare`) and
  `protocolize/tree_anchor.py` (the `${TREE}` spelling). Extend the rule that
  is actually wrong, with a test; do not special-case a project.

The four ways a proof has actually failed, all silently — read them before
concluding your protocol is right, and read the code above for how each is
handled: a module directory (`-J<dir>`) carried into a unit's options; a
flag's ARITY miscounted, so its operand leaks out as a bare input; a
preprocess intermediate leaking into the options; and `-o <path>` kept among
them.

## The OTHER half: how the project says a run was CORRECT

A protocol answers "how is this built?".  The flow also asks "how do we know a
run of it was right?", and that is a **separate artifact**,
`verification_plan.json`, produced by the SAME onboarding pass and committed
the same way (gzipped, expanded on use).  Read this whole half before starting
it, then follow the steps in order.

**`reference/verification-plan-template.json` is the annotated SHAPE.** Every
section carries a `_README` explaining what the field means, what the project
must declare for it, and what was MEASURED when it was got wrong — read it to
understand a field (a value like `atol` is a measurement only this project can
make). Stripping every `_`-prefixed key leaves a plan `problems()` accepts, and
a test pins that. **You do not hand-fill it**: the plan is PRODUCED by the
capture from the project's own declaration, and a field typed by hand is a
value nobody can re-derive.

### V1. Hand the project's own declaration to the capture

The plan's whole content — which cases exist, what each case's input is, what
counts as a difference — is the PROJECT's statement, and the capture reads it
from the project. You do not choose among shapes: `bench.py capture` detects
them (tests the build registered; a case list with an `INCLUDE` chain; one
directory per case) and REFUSES when it finds none or finds two, because an
empty plan is not a neutral answer.

**Two outcomes when it refuses, and they are different — the same split the
build half has:**

* **the declaration is wrong or missing** — the common case. Put the
  project's statement where the capture reads it and pass it in.
* **the capture cannot read a shape this project declares** — a TOOL gap.
  Extend `capture_verification` in `protocolize/onboard/extract.py`, with a
  test, so the next project with that shape is read too. **Do NOT hand-write
  the plan instead**: a plan typed by hand is a value nobody can re-derive,
  and the extractor keeps the gap for every project after this one.

Two things ARE yours, because they are about where you put things:

**Name the checkout after the PROJECT, not after the repository.** A
validation suite kept as its own repository is found as a SIBLING whose name
begins with the project's — so a repository called `<Repo>Solver` whose suite
is `<Project>_Validation` only works when the checkout sits at `<Project>/`.
When the two are fetched separately, place them as siblings:

```
benchmark/012-MYSTRAN/
   MYSTRAN/              <- the checkout, named after the project
   MYSTRAN_Validation/   <- its suite, a sibling
```

Ambiguity is REFUSED rather than resolved by picking one, because picking
silently verifies a corpus against another project's cases.

**The comparison rules are DECLARED, never searched for.** The file that
states them is passed in (`--declaration`), and so are the facts a program
needs in order to RUN at all (`--facts`) — see the fetch skill's section on a
run that reports nothing. A search that widened until it found something would
find a neighbour's.

### V2. Capture the plan, and make the reference

```bash
python3 bench.py capture <build tree> \
    --declaration <the file that states its comparison rules> \
    --facts       <what its programs need in order to run, if any> \
    --out         <where to write verification_plan.json>
```

That is everything in this half that is mechanical, and it includes the
reference: the plan is run against the REFERENCE instance and its output kept
as the golden, so "a failed reference run captures nothing" holds by
construction rather than by anyone remembering it.

**A non-zero exit names the problem, and `problems()` is the authority** — the
refusals are implemented there (`protocolize/verification_plan.py`), not in
this page, and each one is a plan that could produce a verdict with nothing
behind it: no `require_min`; a `process` run that stages no inputs; a
`golden.source: none` with no stamp; a bare `$`; a `golden.root` that names a
file the project SHIPPED. `--facts` is optional; leaving it out when it is
needed is caught further downstream, because the cases then run and report
nothing.

**Why the golden is never the project's own shipped output.** A repository
supplies **input files**; a run supplies the golden. Shipped reference output
came from another machine, compiler and decade — measured: not one of one
corpus's 132 shipped `.out` files is byte-identical to what the same decks
produce here, so using them would be a fixture nothing can satisfy, and a
build that merely copied a stale file would "pass". Comparing against the
project's own reference output is legitimate, and belongs in the project's
`declaration`, never in `golden` — which is why `golden.root` is restricted to
a run output root and anything else is refused when the plan is READ.

### V4. Run §B on the project AND on the replay

Two runs decide the plan, and BOTH must reach the same verdict:

```bash
python3 bench.py verify <benchmark dir>       # §B on the project's own build
python3 bench.py admit  <benchmark dir> --…   # §B on the project AND the replay
```

A pass on the project alone is not the proof: the plan must also be green on the
build the PROTOCOL produces, or the two artifacts disagree.

### V5. If a golden comparison differs, stabilize it by MEASUREMENT

**This applies only when the check COMPARES two texts against a golden** —
the plan's `text_lockstep` and `field_assertion`.  A `self_report` reads
numbers the project printed about itself, `artifact` is existence, `metric` is
one value: none has a golden text, none has a line to suppress, and a
`drop`/`keep`/`mask` written for them is a key the mechanism never reads (the
plan schema refuses such a key by name).

A project that compares a fresh run against a golden will show differences
that are **not** differences in what it computed.  They come from the RUN.
Do this in order:

**Step 1 — MEASURE. Do not eyeball the output.**

```bash
python3 bench.py evidence <benchmark dir> --shift-date
```

This runs the reference TWICE (the second under a shifted clock, so
calendar-derived lines move too) and prints the line SHAPES that moved and
that the plan's own `drop`/`keep`/`mask` do **not** yet cover. Those shapes are
the work list. Two limits are stated by the tool itself: it is a **lower
bound** (a line that happened to agree is invisible), and a shape is evidence
of a **mechanism** (a timer block, a pool report), not of one line.

**Step 2 — recognize the category**, using the table below, then confirm it
against what Step 1 actually reported. The table is HYPOTHESES; the
measurement is the evidence.

| category | what the line looks like |
|---|---|
| **clocks** | a date, a wall/CPU/elapsed time, `N SECONDS`, a "time estimate", a progress percent, uptime, a per-step timer row |
| **memory addresses** | a pointer, a pool/heap/stack base, an allocation offset, `%p`, a bare wide integer |
| **absolute paths** | a build dir, source root, tmp dir, home, install prefix, `argv[0]`, an absolute path inside a diagnostic |
| **process identity** | PID, PPID, TID, session or job id |
| **machine / user identity** | hostname, FQDN, username, uid, domain, node name |
| **randomness / session id** | UUID/GUID, a seed, a nonce, a `mkstemp` name, hash/container iteration order |
| **environment-derived** | CPU count, memory size, load average, free disk, terminal width, locale |
| **filesystem metadata** | a file size, mtime, inode, device number, "bytes written" |
| **concurrency order** | lines, or a float SUM, whose order depends on scheduling |
| **uninitialized / padding** | bytes never assigned; stack garbage in a hex dump |
| **build / version stamps** | a compiler version string, a build date, a VCS revision, "built by" |

**Step 3 — write a rule for each shape, into the project's own declaration**
(`declaration.json`, under `comparison`), never into the tool. Prefer `mask`
over `drop`.

**Step 4 — re-MEASURE.** Re-capture (the declaration is read at capture time)
and run `bench.py evidence` again; it must report nothing left uncovered, and
the verdict must hold on a **fresh capture** — a single PASS proves nothing
about an intermittent difference.

**Deliberately NOT on this list: near-zero floating-point noise** (a residual
around `1e-18` whose sign is meaningless).  That is not a run-dependency — it
is a numerical-tolerance matter, and the plan's `rtol`/`atol` is where it
belongs.  Treating it as a line to drop would stop comparing the magnitude.

**Concurrency order is not a line rule.**  `drop`/`keep`/`mask` act on lines,
and that problem is the line ORDER; a float sum is worse, since reordering
changes the result through non-associativity — a genuine numeric difference.
Such an output needs a comparison that is order-blind (sort before comparing)
or a different assertion kind.  Do not force it into a line pattern.

#### The saturation trap, which runs the other way

A fixed-width field that cannot hold its value **saturates**: an 8-wide
integer field printing a 9-digit number emits a run of `*` instead.  **Two
DIFFERENT values then print as the SAME string and compare EQUAL** — so the
error direction is always a **false PASS**, and a line that saturates in both
runs looks like a perfectly stable constant until one run happens to fit.

So **"this line looks stable" does NOT mean "this line did not move."**  For a
fixed-width field, stability is the symptom, not the evidence.  To confirm a
suspected saturation: **widen the field temporarily and rebuild**, run several
times, and see whether the values are then all different.

Such a field almost always shares its line with stable, meaningful values, so
it must be **`mask`ed, not dropped** — dropping the line stops comparing the
rest of it too.

**Never classify a diff as "reasonable" and move on.**  That is exactly how a
real difference gets hidden: an automatic "this looks like a timestamp, skip
it" is the same failure as any other check that did nothing while reporting
success.  The categories above are HYPOTHESES — a measurement confirms them,
and a rule suppresses exactly one measured quantity and nothing else.

## Available tools

The operations are `bench.py` subcommands — use them, in this order for the
build half:

```bash
python3 bench.py extract <build tree> --out <protocol.json>
# §A: reads the protocol OUT of the built tree and PROVES the replay.
# Non-zero exit = the protocol does not replay the project; the reason is
# printed and that reason IS the next thing to fix.

python3 bench.py capture <build tree> --declaration <rules file> [--facts <f>]
# The verification half: reads the plan out of the project's own declaration,
# and refuses if the plan is not executable.

python3 bench.py cases <build tree>
# The case ids the plan discovers, one per line. Run it whenever a golden
# comparison looks wrong: a plan that discovers ZERO cases is the failure a
# whole comparison hides behind, and this shows it in one line.
```

`protocolize/onboard/` holds the working implementation of all of the above
for ninja and make, and `onboard/selfcheck.py` holds the §A proof. Read them
as a reference for the method — and as something you may extend, replace or
ignore if the project in front of you needs a different approach. The method
above is the contract; those files are one way of meeting it.

**When an operation you need does not exist as a subcommand, do not write it
beside one.** Report it: a re-implementation drifts from the original in some
detail that stays invisible until it bites. Measured twice — a re-derived
"which directory is the tree" answered wrongly for a checkout with no
build-system marker at the top, and a re-derived "clear the build" deleted a
make project's source directory.

## What this skill does not do

It does not convert anything, run any pipeline, or build the project. It reads
one build and writes one file. Everything after that is the flow's.
