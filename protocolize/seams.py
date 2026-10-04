"""What a project needs from judgement, as TYPED CONTRACTS.

The flow is a program (`admit.py`) that fills in a form: a build protocol and a
verification plan. Almost every field is read out of the project — the build
system is asked for its own commands, the tree is asked for its cases. Four
fields are not derivable, because they are facts about a project that no build
system prints and no tree records:

* how to build and verify it, when its own build system is not the default;
* the patches it needs before it builds at all;
* what its programs need in order to run (a home directory, a resource tree);
* how two runs of it are legitimately COMPARED (volatile lines, numeric slack).

Those four are where judgement is needed, and they were previously found by an
agent reading a 189-line procedure and hand-writing flags. Here they are
DECLARATIONS with a schema, so the flow can validate what comes back instead
of trusting it. A fifth response exists for the case the form itself is
inadequate:

* `protocol_gap` — the form cannot express what this project needs.

That one is NOT a failure and is NOT retried. It says the protocol has to
grow, and growing it is the main conversation's job, never a seam's.

Why the contract is this small: these five are exactly the inputs the flow
already accepted (`fetch.py`'s `--configure/--build/--test/--tree-subdir`,
`--prepare`, `--facts`, `--declaration`). Nothing new is being invented — the
difference is that the flow now ASKS for them instead of waiting to be told.

Every response is validated by RUNNING it, not by reading it: a
`native_commands` is adopted only once it builds and verifies, a `prepare_ops`
only once its own `check` can decide it and the native build passes after it.
A script cannot judge intent — it can only refuse what it cannot confirm.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: The kinds a seam may answer with.  A response naming anything else is
#: refused rather than guessed at: an unrecognised kind means the contract the
#: flow was built against is not the contract the answer was written to.
KINDS = ('native_commands', 'prepare_ops', 'facts', 'declaration',
         'protocol_gap')

#: The keys a `facts` answer may carry — what `capture_verification` actually
#: READS (`extract.py`: `_staging_facts`, `_chains_from`, `_declared_produced`,
#: `_golden_place`, `_case_list_assertions`).
#:
#: Measured, and the reason this is validated rather than trusted: a `facts`
#: answer that used top-level `env` / `home` / `config` / `resources` instead
#: of `preparation.home_env` / `preparation.resource_dir` passed every check —
#: `problems` only asked for a dict — and then did NOTHING. The capture read
#: `preparation`, found none, shipped `"staging": []`, the reference run had no
#: environment, its program exited 0 having written nothing, and the verdict
#: was "reference run captured no goldens". A wrong-shaped answer that is
#: silently accepted is the vacuous-verification class this whole flow exists
#: to refuse.
FACTS_KEYS = ('artifact', 'field_model', 'chains', 'preparation', '_comment')

#: The `preparation` keys, likewise.  These are what a program needs in order
#: to RUN: a home directory, a resource tree, a program config.  They are
#: stated in the seam prompt so the answer is shapeable without reading the
#: capture's source, which is a thing no agent should have to do.
PREPARATION_KEYS = ('home_env', 'resource_dir', 'scratch_subdir',
                    'config_env', 'config_glob', 'substitutions',
                    'alias_upper', 'env')


class SeamError(RuntimeError):
    """A seam's answer could not be used.  Never a verdict about a project."""


@dataclass
class Response:
    """One seam's answer: its kind, its payload, and the raw text it came in.

    The raw text is kept because the payload is what the flow validated and
    the raw is what a reader needs when validation fails — a refusal that
    shows only the parsed form hides the shape the answer actually had.
    """
    kind: str
    payload: dict
    raw: str = ''
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _first_object(text: str) -> dict:
    """The first JSON OBJECT in *text*, or raise.

    A model wraps an answer in prose and a code fence.  Measured on a real
    seam: the model wrote "I have enough evidence. …" then a ```json fence,
    then a complete and CORRECT `native_commands` ending `"tree_subdir":
    "source"` — and the run threw it away, because `result` was required to be
    nothing but JSON.  Three earlier runs had happened to emit bare JSON, so
    the requirement looked safe.

    Scanning for the object rather than requiring the whole string to be one
    is not laxity: the `kind` and the schema are still enforced on what comes
    back, so this decides only WHICH text is read, never whether it is
    acceptable.
    """
    s = text.strip()
    if s.startswith('```'):
        # Strip a fence and an optional language tag.
        body = s[3:]
        nl = body.find('\n')
        if nl != -1:
            body = body[nl + 1:]
        end = body.rfind('```')
        s = (body[:end] if end != -1 else body).strip()
    try:
        obj = json.loads(s)
    except (TypeError, ValueError):
        obj = None
    if isinstance(obj, dict):
        return obj
    # Not JSON on its own: find the outermost object in the prose.  `raw_decode`
    # from each `{` is what makes a nested object's braces not confuse it.
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch != '{':
            continue
        try:
            cand, _ = dec.raw_decode(s[i:])
        except ValueError:
            continue
        if isinstance(cand, dict):
            return cand
    raise SeamError('the answer holds no JSON object')


def parse(text: str) -> Response:
    """Read a seam's answer.  Structure only — `check` decides the rest.

    Accepts the answer as a JSON object, optionally wrapped in the envelope a
    print-mode agent returns (`{"result": ...}`), because that envelope is a
    property of the CALLER's tool, not of this contract.  An envelope marked
    as an error is refused, and so is one whose payload is not an object: a
    bare string has no `kind`, and guessing one would be inventing the
    contract instead of enforcing it.
    """
    try:
        obj = json.loads(text)
    except (TypeError, ValueError) as exc:
        # The whole answer may be prose around an object, not an envelope at
        # all — measured, a model answered a seam with a sentence, a fenced
        # ```json block, and nothing else.  Structure is still enforced on
        # what comes back; this only decides which text is read.
        try:
            obj = _first_object(text)
        except SeamError:
            raise SeamError(f'the answer is not JSON: {exc}') from exc

    if isinstance(obj, dict) and 'result' in obj and 'kind' not in obj:
        if obj.get('is_error'):
            raise SeamError(f'the agent reported an error: {obj.get("result")!r}')
        obj = obj['result']
        if isinstance(obj, str):
            obj = _first_object(obj)
    if not isinstance(obj, dict):
        raise SeamError(f'the answer is a {type(obj).__name__}, not an object')

    kind = obj.get('kind')
    if not kind:
        raise SeamError('the answer has no `kind`: a seam answer must say '
                        'which of ' + ', '.join(KINDS) + ' it is')
    if kind not in KINDS:
        raise SeamError(f'`kind` is {kind!r}, which is not one of '
                        + ', '.join(KINDS))
    return Response(kind=kind, payload=obj, raw=text)


def problems(resp: Response) -> list[str]:
    """What is wrong with *resp*, in the terms the flow can act on.

    Empty for `protocol_gap`: it is a STATEMENT, not a filled-in field, so
    there is nothing to be wrong with it beyond naming what is missing.
    """
    p = resp.payload
    kind = resp.kind
    bad: list[str] = []

    if kind == 'native_commands':
        for key in ('configure', 'build', 'test'):
            v = p.get(key)
            if v is None:
                continue
            if isinstance(v, str):
                continue
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                continue
            bad.append(f'`{key}` must be a shell line or a list of them')
        if not p.get('build'):
            bad.append('`build` is empty: a project that needs this seam has '
                       'a build, and not naming it leaves the flow with '
                       'nothing to run')
        if 'tree_subdir' in p and not isinstance(p['tree_subdir'], str):
            bad.append('`tree_subdir` must be a string')

    elif kind == 'prepare_ops':
        ops = p.get('ops')
        if not isinstance(ops, list) or not ops:
            bad.append('`ops` must be a non-empty list of patches')
        else:
            for i, op in enumerate(ops):
                if not isinstance(op, dict):
                    bad.append(f'ops[{i}] is not an object')
                    continue
                if not op.get('run'):
                    bad.append(f'ops[{i}] has no `run`: a patch that runs '
                               f'nothing changes nothing')
                if not op.get('check'):
                    # The load-bearing one.  `check` is what makes a patch
                    # idempotent AND confirmable; without it the flow cannot
                    # tell an applied patch from an unapplied one, and the
                    # op would run again on every admission.
                    bad.append(f'ops[{i}] has no `check`: without one the '
                               f'flow cannot tell whether the patch is '
                               f'already applied, and it is neither '
                               f'reproducible nor idempotent')

    elif kind == 'facts':
        value = p.get('value')
        if not isinstance(value, dict):
            bad.append('`value` must be the object to hand the capture')
        else:
            known = [k for k in value if k in FACTS_KEYS]
            if not known:
                # Not pedantry: a `facts` object whose keys the capture does
                # not read is INERT.  It passes every other check, changes
                # nothing, and the run then reports a failure about the
                # project.  See FACTS_KEYS for the measurement.
                bad.append(
                    '`value` has no key the capture reads '
                    f'(gave {sorted(value)}; it reads {list(FACTS_KEYS)}) — '
                    'an inert declaration is not a declaration')
            prep = value.get('preparation')
            if prep is not None:
                if not isinstance(prep, dict):
                    bad.append('`preparation` must be an object')
                else:
                    unknown = [k for k in prep if k not in PREPARATION_KEYS]
                    if unknown:
                        bad.append(
                            f'`preparation` has unknown key(s) {sorted(unknown)}'
                            f'; it reads {list(PREPARATION_KEYS)}')

    elif kind == 'declaration':
        value = p.get('value')
        if not isinstance(value, dict):
            bad.append('`value` must be the object to hand the capture')
        elif 'comparison' not in value:
            # The capture reads `comparison.drop/rtol/atol` and nothing else.
            bad.append(
                '`value` has no `comparison`: the capture reads '
                '`comparison.{drop,rtol,atol}` and would take nothing from '
                'this')

    elif kind == 'protocol_gap':
        if not p.get('missing'):
            bad.append('`missing` must name what the protocol cannot express')

    return bad
