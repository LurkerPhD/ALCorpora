"""Seam A: a failed native build, and what the flow will and will not adopt.

Two properties carry the design, and both are enforced by RUNNING things
rather than by trusting the answer:

* an unusable answer is never adopted — a response whose kind does not
  resolve a build failure changes nothing;
* a patch is recorded only if it MADE THE BUILD WORK. "Never record a fix
  that did not make the build work" is enforced by the loop's structure: the
  flow re-runs the native step after adopting, and a proposal that does not
  fix it is discarded by that run failing again.

A `protocol_gap` is neither: it is the agent saying the FORM is inadequate,
so the flow stops rather than guessing, and expanding the form is the main
conversation's job.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import admit


class _FakeAgent:
    """Answers with whatever it was constructed with, once per call."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.log_dir = None
        self.asked: list[str] = []

    def ask(self, seam, prompt, cwd, *, signature=''):
        self.calls += 1
        self.asked.append(seam)
        from alcorpora import seams
        text = (self.payload if isinstance(self.payload, str)
                else json.dumps(self.payload))
        resp = seams.parse(text)
        bad = seams.problems(resp)
        if bad:
            from alcorpora.agent import SeamFailed
            raise SeamFailed('; '.join(bad), raw=text)
        return resp


def _args(**over):
    class A:
        configure = 'cmake -S {src} -B {src}/build -G Ninja'
        build = 'cmake --build {src}/build'
        test = 'ctest --test-dir {src}/build'
        tree_subdir = 'build'
        prepare_ops: list = []
        facts = None
        declaration = None
    a = A()
    for k, v in over.items():
        setattr(a, k, v)
    return a


def _namespace(native):
    return {'native': native, '_require_present': lambda p, w: p,
            '_declared': lambda v, s: None}


def test_a_native_commands_answer_replaces_the_commands(tmp_path: Path):
    src = tmp_path / 'tree'
    src.mkdir()
    runs: list = []

    def native(s, args):
        runs.append((args.configure, args.build, args.test))
        return (len(runs) > 1), 'built\n', []

    args = _args()
    agent = _FakeAgent({'kind': 'native_commands',
                        'configure': '', 'build': 'make -C src',
                        'test': './check.sh'})
    ok, log, _ = admit._native_with_seams(_namespace(native), args, agent,
                                          src, tmp_path)
    assert ok
    assert runs[0] != runs[1], 'the second run must use the new commands'
    assert runs[1] == ('', 'make -C src', './check.sh')


def test_a_patch_is_adopted_and_then_judged_by_the_build(tmp_path: Path):
    src = tmp_path / 'tree'
    src.mkdir()
    seen: list = []

    def native(s, args):
        seen.append(list(args.prepare_ops))
        return (len(seen) > 1), 'built\n', list(args.prepare_ops)

    args = _args()
    agent = _FakeAgent({'kind': 'prepare_ops', 'ops': [
        {'op': 'fix', 'check': 'test -f done', 'run': 'touch done'}]})
    ok, log, remedies = admit._native_with_seams(_namespace(native), args,
                                                 agent, src, tmp_path)
    assert ok
    assert seen[0] == [], 'the first run had no patches'
    assert len(seen[1]) == 1, 'the patch must be in force for the second run'


def test_a_patch_that_does_not_fix_the_build_is_not_kept(tmp_path: Path):
    """The structural guarantee: the build decides, not the answer."""
    src = tmp_path / 'tree'
    src.mkdir()

    def native(s, args):
        return False, 'still broken\n', list(args.prepare_ops)

    args = _args()
    agent = _FakeAgent({'kind': 'prepare_ops', 'ops': [
        {'op': 'nope', 'check': 'false', 'run': 'true'}]})
    ok, log, _ = admit._native_with_seams(_namespace(native), args, agent,
                                          src, tmp_path)
    assert not ok, 'a build that never succeeds is never LEGAL'
    # The loop stopped rather than asking again forever.
    assert agent.calls <= admit.MAX_SEAM_PASSES


def test_an_answer_that_cannot_resolve_a_build_is_not_adopted(tmp_path: Path):
    src = tmp_path / 'tree'
    src.mkdir()
    runs: list = []

    def native(s, args):
        runs.append(1)
        return False, 'broken\n', []

    args = _args()
    agent = _FakeAgent({'kind': 'declaration', 'value': {'comparison': {}}})
    ok, log, _ = admit._native_with_seams(_namespace(native), args, agent,
                                          src, tmp_path)
    assert not ok
    assert args.prepare_ops == []
    assert len(runs) == 1, 'nothing changed, so nothing should be re-run'


def test_a_protocol_gap_stops_instead_of_retrying(tmp_path: Path, capsys):
    src = tmp_path / 'tree'
    src.mkdir()
    runs: list = []

    def native(s, args):
        runs.append(1)
        return False, 'broken\n', []

    args = _args()
    agent = _FakeAgent({'kind': 'protocol_gap',
                        'missing': 'how this project is built',
                        'evidence': 'no build system in the tree'})
    ok, _, _ = admit._native_with_seams(_namespace(native), args, agent,
                                        src, tmp_path)
    assert not ok
    assert agent.calls == 1, 'a gap is reported, never retried'
    assert len(runs) == 1
    err = capsys.readouterr().err
    assert 'PROTOCOL_GAP' in err
    assert 'how this project is built' in err


def test_an_unreachable_agent_leaves_the_seam_closed(tmp_path: Path):
    """With no agent reachable, a failed build simply fails — cleanly.

    This IS the fallback: an agent that cannot be run leaves the seam closed,
    so the step reports the failure it actually had."""
    src = tmp_path / 'tree'
    src.mkdir()

    def native(s, args):
        return False, 'broken\n', []

    ok, log, _ = admit._native_with_seams(_namespace(native), _args(), None,
                                          src, tmp_path)
    assert not ok
    assert 'broken' in log


def test_omitting_configure_CLEARS_it(tmp_path: Path):
    """The contract says omit `configure` when there is nothing to configure.

    Measured on a make project: the agent answered `native_commands` with no
    `configure` (correct — a hand-written Makefile has no configure step), and
    the flow ADOPTED it but left the CMake default in place, because only keys
    present in the answer were overwritten. The build then failed on `cmake -S
    ...` again, and a make project could never build through the seam.
    """
    src = tmp_path / 'tree'
    src.mkdir()
    runs: list = []

    def native(s, args):
        runs.append((args.configure, args.build, args.tree_subdir))
        return (len(runs) > 1), 'built\n', []

    args = _args()
    agent = _FakeAgent({'kind': 'native_commands',
                        'build': ['make -C source/mis', 'make -C source/mds'],
                        'tree_subdir': 'source'})
    admit._native_with_seams(_namespace(native), args, agent, src, tmp_path)

    configure, build, tree_subdir = runs[1]
    assert configure == '', (
        'an omitted configure must CLEAR the default, not leave CMake in '
        f'place: {configure!r}')
    assert build == 'make -C source/mis && make -C source/mds'
    assert tree_subdir == 'source'


def test_a_list_answer_is_joined_in_order(tmp_path: Path):
    src = tmp_path / 'tree'
    src.mkdir()
    args = _args()
    admit._apply_native_commands(args, {'build': ['make -C a', 'make -C b']})
    assert args.build == 'make -C a && make -C b'


def test_a_wrong_tree_subdir_is_asked_again(tmp_path: Path):
    """A build that works is necessary and not sufficient.

    Measured: a real agent named a make project's commands correctly
    (`make -C source/mis`, `make -C source/mds`) and omitted `tree_subdir`, so
    the default `build` survived, the native build PASSED, and §A then failed
    on a directory holding no build system. §A failing has no seam — that is a
    TOOL defect — so the run could not return to the seam that had just
    answered. The check belongs where the answer is still the subject.
    """
    src = tmp_path / 'tree'
    (src / 'source' / 'mds').mkdir(parents=True)
    (src / 'source' / 'mds' / 'Makefile').write_text('all:\n\t@true\n')

    answers = [
        {'kind': 'native_commands', 'build': ['make -C source/mds']},  # no tree_subdir
        {'kind': 'native_commands', 'build': ['make -C source/mds'],
         'tree_subdir': 'source'},                                     # corrected
    ]

    class _Seq:
        calls = 0
        log_dir = None

        def ask(self, seam, prompt, cwd, *, signature=''):
            from alcorpora import seams
            import json as _j
            payload = answers[min(self.calls, len(answers) - 1)]
            self.calls += 1
            return seams.parse(_j.dumps(payload))

    args = _args()
    agent = _Seq()
    runs: list = []

    def native(s, a):
        # The DEFAULT commands fail first — that is what opens seam A. After
        # an answer arrives the build works, which is the case this test is
        # about: a build that succeeds tells you nothing about whether §A will
        # be able to READ the tree.
        runs.append(a.tree_subdir)
        return (len(runs) > 1), 'built\n', []

    ok, log, _ = admit._native_with_seams(_namespace(native), args, agent,
                                          src, tmp_path)
    assert agent.calls >= 2, (
        'a build that worked against an unreadable tree must send the answer '
        'back to the seam, not be accepted')
    assert runs == ['build', 'build', 'source'], (
        f'the default, the first answer, then the corrected answer: {runs}')
    assert ok, 'the corrected answer resolves it'
