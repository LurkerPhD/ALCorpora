"""Which failed step opens a seam, and which does not.

The distinction is load-bearing and was a correction: the plan first said a
§B failure could never be a seam, which would have made the flow treat "this
project needs a patch" as "our tool is broken" — and refuse to patch.

Two steps still have no seam:

* §A failing is a TOOL defect. Opening a seam there sends the agent to work
  around a broken extractor instead of getting it repaired.
* §B failing on the REPLAY means the protocol and the build disagree, which
  is likewise ours.

The steps that do open one are those where the missing thing is a fact about
the PROJECT: a declaration nobody supplied (D), or a patch it needs (E).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import admit
from protocolize.agent import SeamFailed


class _FakeAgent:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.log_dir = None

    def ask(self, seam, prompt, cwd, *, signature=''):
        self.calls += 1
        from protocolize import seams
        text = json.dumps(self.payload)
        resp = seams.parse(text)
        bad = seams.problems(resp)
        if bad:
            raise SeamFailed('; '.join(bad), raw=text)
        return resp


def _module():
    """The mechanical half, loaded by path (its directory is not a package)."""
    import importlib.util
    import sys
    repo = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'fetch_skill_seams', repo / 'skills/fetch-benchmark-project/'
                                   'scripts/fetch.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(tmp_path: Path, steps, agent, prepare_ops=None):
    """Drive `_prove_with_seams` with a scripted sequence of proof results.

    *steps* is the sequence of `(ok, step)` the mechanical half should report
    on each call, so a test can say "fails, then succeeds" without building a
    project.
    """
    M = _module()
    seq = list(steps)

    def prove_legal(dest, src, build, number, name, remedies, **kw):
        ok, step = seq.pop(0) if seq else (True, 'legal')
        if ok:
            return M.Proof(True, 'legal', 'LEGAL',
                           {'status': 'PASS', 'language': 'c'}, 'c')
        return M.Proof(False, step, f'{step} failed',
                       {'status': 'FAIL_B_REF'}, 'c')

    M.prove_legal = prove_legal
    M.native = lambda src, args: (True, 'built', list(args.prepare_ops))
    dest = tmp_path / '003-thing'
    src = dest / 'thing'
    src.mkdir(parents=True)

    class A:
        configure = build = test = tree_subdir = 'build'
        facts = declaration = None
    args = A()
    args.prepare_ops = list(prepare_ops or [])
    out = admit._prove_with_seams(dict(M.__dict__), args, agent, dest, src,
                                  3, 'thing', [], 'fetched from source')
    return out, args, M


def test_a_recovers_without_a_seam(tmp_path: Path):
    proof, args, M = _run(tmp_path, [(True, 'legal')], None)
    assert proof.ok


def test_step_A_never_opens_a_seam(tmp_path: Path):
    """A tool defect is not the agent's to work around."""
    agent = _FakeAgent({'kind': 'protocol_gap', 'missing': 'x'})
    proof, args, M = _run(tmp_path, [(False, 'A')], agent)
    assert not proof.ok
    assert agent.calls == 0, '§A failing must not ask an agent'


def test_the_replay_failing_never_opens_a_seam(tmp_path: Path):
    agent = _FakeAgent({'kind': 'facts', 'value': {}})
    proof, args, M = _run(tmp_path, [(False, 'B-flat')], agent)
    assert not proof.ok
    assert agent.calls == 0, 'protocol vs build disagreement is ours to fix'


def test_a_plan_failure_opens_seam_D_and_takes_a_declaration(tmp_path: Path):
    agent = _FakeAgent({'kind': 'declaration',
                        'value': {'comparison': {'drop': ['^T']}}})
    proof, args, M = _run(tmp_path, [(False, 'plan'), (True, 'legal')], agent)
    assert proof.ok
    assert agent.calls == 1
    assert getattr(args, '_seam_declaration', None) == {
        'comparison': {'drop': ['^T']}}


def test_a_no_goldens_failure_opens_seam_D_too(tmp_path: Path):
    agent = _FakeAgent({'kind': 'facts', 'value': {'preparation': {}}})
    proof, args, M = _run(tmp_path, [(False, 'ref'), (True, 'legal')], agent)
    assert proof.ok
    assert getattr(args, '_seam_facts', None) == {'preparation': {}}


def test_a_project_B_failure_opens_seam_E_and_accepts_a_patch(tmp_path: Path):
    agent = _FakeAgent({'kind': 'prepare_ops', 'ops': [
        {'op': 'fix', 'check': 'test -f done', 'run': 'touch done'}]})
    proof, args, M = _run(tmp_path, [(False, 'B-project'), (True, 'legal')],
                          agent)
    assert proof.ok
    assert len(args.prepare_ops) == 1


def test_a_patch_that_does_not_build_is_refused(tmp_path: Path):
    """The structural guarantee, at seam E as at seam A."""
    M = _module()
    seq = [(False, 'B-project')]

    def prove_legal(dest, src, build, number, name, remedies, **kw):
        ok, step = seq.pop(0) if seq else (True, 'legal')
        return M.Proof(ok, step, 'x', {'status': 'PASS'}, 'c')

    M.prove_legal = prove_legal
    M.native = lambda src, args: (False, 'does not compile', [])
    dest = tmp_path / '003-thing'
    src = dest / 'thing'
    src.mkdir(parents=True)

    class A:
        configure = build = test = tree_subdir = 'build'
        facts = declaration = None

    args = A()
    args.prepare_ops = []
    agent = _FakeAgent({'kind': 'prepare_ops', 'ops': [
        {'op': 'bad', 'check': 'false', 'run': 'true'}]})
    proof = admit._prove_with_seams(dict(M.__dict__), args, agent, dest, src,
                                    3, 'thing', [], None)
    assert not proof.ok, 'a patch that breaks the build must not be accepted'


def test_no_agent_closes_every_seam(tmp_path: Path):
    proof, args, M = _run(tmp_path, [(False, 'plan')], None)
    assert not proof.ok
    assert proof.step == 'plan'


def test_a_protocol_gap_stops_at_seam_E(tmp_path: Path, capsys):
    agent = _FakeAgent({'kind': 'protocol_gap',
                        'missing': 'how this is verified',
                        'evidence': 'no runner'})
    proof, args, M = _run(tmp_path, [(False, 'B-project')], agent)
    assert not proof.ok
    assert agent.calls == 1, 'a gap is reported, never retried'
    assert 'PROTOCOL_GAP' in capsys.readouterr().err


def test_an_answer_that_cannot_resolve_the_step_is_not_adopted(tmp_path: Path):
    """A `facts` answer cannot fix a step that wants a patch."""
    agent = _FakeAgent({'kind': 'facts', 'value': {'preparation': {}}})
    proof, args, M = _run(tmp_path, [(False, 'B-project'), (False, 'B-project')],
                          agent)
    assert not proof.ok
    # It was adopted once (a new declaration IS usable input), the proof ran
    # again, failed the same way, and the flow stopped rather than looping.
    assert agent.calls <= admit.MAX_SEAM_PASSES


def test_a_patch_that_works_is_carried_into_the_protocol(tmp_path: Path):
    """`remedies` is what `prove_legal` bakes into `proto.prepare`.

    Carrying the ORIGINAL remedies forward would apply the patch to this tree
    and then leave it out of the protocol — so a fresh checkout would build a
    project nobody patched, and the admission would not be reproducible.
    """
    M = _module()
    seen: list = []
    seq = [(False, 'B-project'), (True, 'legal')]

    def prove_legal(dest, src, build, number, name, remedies, **kw):
        seen.append(list(remedies or []))
        ok, step = seq.pop(0)
        return M.Proof(ok, step, 'x', {'status': 'PASS'}, 'c')

    M.prove_legal = prove_legal
    M.native = lambda src, args: (True, 'built', list(args.prepare_ops))
    dest = tmp_path / '003-thing'
    src = dest / 'thing'
    src.mkdir(parents=True)

    class A:
        configure = build = test = tree_subdir = 'build'
        facts = declaration = None

    args = A()
    args.prepare_ops = []
    agent = _FakeAgent({'kind': 'prepare_ops', 'ops': [
        {'op': 'fix-paths', 'check': 'test -f /tmp/x', 'run': 'true'}]})
    admit._prove_with_seams(dict(M.__dict__), args, agent, dest, src, 3,
                            'thing', [], None)

    assert seen[0] == [], 'the first pass had no patches'
    assert len(seen[1]) == 1 and seen[1][0]['op'] == 'fix-paths', (
        f'the working patch must reach the protocol: {seen[1]}')


def test_a_repeated_seam_is_told_what_is_already_in_force(tmp_path: Path):
    """A repeated question must not be answered as a DELTA.

    Measured on a real run, and it cost the whole verdict: seam E was asked,
    the agent answered 14 volatile-line patterns, §B then failed on 7 more
    cases, and the flow asked again. The second answer held TWO patterns — the
    ones for the newly-failing lines — and the flow used it as the whole set.
    125 passed; the 7 that failed were failing on the patterns the second
    answer had dropped.

    The agent answered the question it was given, which is why the fix is in
    the flow: it has to say what is in force and ask for the whole set.
    """
    class A:
        _seam_declaration = {'comparison': {'drop': ['^DATE', '^END TIME']}}

    note = admit._replace_not_extend('E', A())
    assert '^DATE' in note and '^END TIME' in note, note
    assert 'REPLACES' in note and 'silently un-declares' in note, note

    class B:
        pass

    assert admit._replace_not_extend('E', B()) == '', (
        'the first ask has nothing in force to report')
    assert admit._replace_not_extend('A', A()) == '', (
        'only a declaration replaces an earlier one')


def test_the_seam_prompt_carries_a_tree_census(tmp_path: Path):
    """The listing is handed over instead of fought over.

    Measured, and it is where the money went: a seam agent's answer cost
    $13-31 and 3.5-6.7 MILLION input tokens, 98% of them cache reads — it was
    re-reading the conversation on every retry, and retrying because every way
    it tried to LOOK at the tree was refused (13-15 denials per seam).

    The agent is meant to read the tree; it just must not BUILD or RUN the
    project. Refusing the reading turned a needed fact into a fight, and the
    fight was the cost.
    """
    tree = tmp_path / 'proj'
    (tree / 'source' / 'mds').mkdir(parents=True)
    (tree / 'source' / 'mds' / 'Makefile').write_text('all:\n\t@true\n')
    (tree / 'README.md').write_text('hi\n')

    census = admit._tree_census(tree)
    assert 'README.md' in census, census
    assert 'source/' in census and 'mds/' in census, (
        'two levels: the tree and its immediate children. Deeper and it stops '
        f'being a listing and becomes the tree. Got: {census!r}')

    # It must not be a substitute for reading: the prompt still says to read.
    prompt = admit._seam_prompt('A', tree, 'situation', 'want', census)
    assert 'read any file you\nneed in full' in prompt
    assert 'DO NOT BUILD OR RUN' in prompt


def test_the_census_is_small(tmp_path: Path):
    """It is a listing, not the tree. A big prompt is the thing being fixed."""
    tree = tmp_path / 'proj'
    tree.mkdir()
    for i in range(500):
        (tree / f'f{i:04d}.c').write_text('x\n')
    assert len(admit._tree_census(tree)) < 4000


def test_the_signature_ignores_timestamps():
    """The seam cache must be able to HIT.

    Measured why the obvious key fails: the signature was
    `hash(step + detail)`, and `detail` carries what §B printed — elapsed
    times and timestamps included. So the same failure produced a new
    signature every minute, the cache never hit, and the agent answered
    afresh each time. A cache that cannot hit is not a determinism guarantee.
    """
    M = _module()

    def proof(detail):
        return M.Proof(False, 'B-project', detail, {'status': 'FAIL'}, 'c')

    base = ('§B failed on the project\'s own build (exit 1)\n'
            'FAIL T03-12-1A (1 diffs) (0.0s)\n'
            "    line 157: '0 RESTART T03121A ,NASTRAN ,10/ 4/26, 16804,' != '...16805,'\n"
            'FAIL T04-02-1A (1 diffs) (0.0s)\n'
            'PASS: 125, FAIL: 7, TOTAL: 132\n'
            'Benchmark Total CPU time: 4.717s\n')
    later = base.replace('16804', '19876').replace('4.717s', '9.001s')

    assert admit._failure_signature(proof(base)) == \
        admit._failure_signature(proof(later)), (
        'the same failure one minute later must reuse the same answer')
    # ... but a DIFFERENT failure must not collide.
    other = base.replace('FAIL: 7', 'FAIL: 3')
    assert admit._failure_signature(proof(base)) != \
        admit._failure_signature(proof(other))
