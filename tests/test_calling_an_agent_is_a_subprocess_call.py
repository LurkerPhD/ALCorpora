"""Calling an agent is a subprocess call, and its failures are its own.

The tests here never invoke a real LLM: the command is a parameter, so a
script that reads stdin and prints a fixed answer stands in for one. That is
the point of the design — the flow's behaviour at a seam is testable without
a model, and an agent that is absent is an INFRASTRUCTURE failure, never a
verdict about a project.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from alcorpora import agent as agent_mod
from alcorpora import seams


def _fake(tmp_path: Path, body: str) -> str:
    """A stand-in agent: a script that prints *body* and exits 0."""
    p = tmp_path / 'fake_agent.py'
    p.write_text(f'import sys\nsys.stdin.read()\nprint({body!r})\n',
                 encoding='utf-8')
    return f'{sys.executable} {p}'


def _failing(tmp_path: Path, code: int, err: str) -> str:
    p = tmp_path / 'bad_agent.py'
    p.write_text(f'import sys\nprint({err!r}, file=sys.stderr)\n'
                 f'sys.exit({code})\n', encoding='utf-8')
    return f'{sys.executable} {p}'


def test_a_good_answer_comes_back_validated(tmp_path: Path):
    a = agent_mod.Agent(cmd=_fake(tmp_path, json.dumps(
        {'kind': 'facts', 'value': {'preparation': {'home_env': 'H'}}})))
    resp = a.ask('B', 'what does this project need?', tmp_path)
    assert resp.kind == 'facts'
    assert resp.payload['value'] == {'preparation': {'home_env': 'H'}}


def test_an_absent_command_is_unavailable_not_a_project_verdict(tmp_path: Path):
    a = agent_mod.Agent(cmd=str(tmp_path / 'no-such-binary'))
    with pytest.raises(agent_mod.AgentUnavailable):
        a.ask('A', 'x', tmp_path)


def test_no_configured_command_is_unavailable(tmp_path: Path):
    with pytest.raises(agent_mod.AgentUnavailable, match='no agent command'):
        agent_mod.Agent(cmd='').ask('A', 'x', tmp_path)


def test_a_crashing_agent_is_unavailable(tmp_path: Path):
    a = agent_mod.Agent(cmd=_failing(tmp_path, 3, 'kaboom'))
    with pytest.raises(agent_mod.AgentUnavailable, match='exited 3'):
        a.ask('A', 'x', tmp_path)


def test_an_unusable_answer_is_a_seam_failure(tmp_path: Path):
    """The agent answered; the answer is not a contract. Not infrastructure."""
    a = agent_mod.Agent(cmd=_fake(tmp_path, 'I think it uses CMake'))
    with pytest.raises(agent_mod.SeamFailed):
        a.ask('A', 'x', tmp_path)


def test_an_incomplete_answer_is_a_seam_failure(tmp_path: Path):
    bad = json.dumps({'kind': 'prepare_ops',
                      'ops': [{'op': 'x', 'run': 'sed -i s/a/b/ f'}]})
    a = agent_mod.Agent(cmd=_fake(tmp_path, bad))
    with pytest.raises(agent_mod.SeamFailed, match='no `check`'):
        a.ask('A', 'x', tmp_path)


def test_the_raw_answer_survives_a_refusal(tmp_path: Path):
    """A refusal must hand back what the agent actually said."""
    raw = 'not json at all'
    a = agent_mod.Agent(cmd=_fake(tmp_path, raw))
    with pytest.raises(agent_mod.SeamFailed) as caught:
        a.ask('A', 'x', tmp_path)
    assert raw in caught.value.raw


def test_the_answer_is_written_to_disk(tmp_path: Path):
    logs = tmp_path / 'logs'
    a = agent_mod.Agent(cmd=_fake(tmp_path, json.dumps(
        {'kind': 'facts', 'value': {'preparation': {}}})), log_dir=logs)
    a.ask('B', 'x', tmp_path, signature='no-output')
    written = list(logs.glob('*.json'))
    assert len(written) == 1, written
    assert 'no-output' in written[0].read_text()


def test_the_same_failure_is_answered_once(tmp_path: Path):
    """Determinism: a repeated run must not get a different answer.

    Otherwise two admissions of the same project, failing the same way, would
    record different commands — and the corpus would stop being comparable
    with itself.
    """
    counter = tmp_path / 'count'
    script = tmp_path / 'counting_agent.py'
    script.write_text(
        'import sys, pathlib, json\n'
        'sys.stdin.read()\n'
        f"c = pathlib.Path({str(counter)!r})\n"
        'n = int(c.read_text()) + 1 if c.exists() else 1\n'
        'c.write_text(str(n))\n'
        "print(json.dumps({'kind': 'facts', 'value': "
        "{'preparation': {'home_env': 'H' + str(n)}}}))\n",
        encoding='utf-8')
    a = agent_mod.Agent(cmd=f'{sys.executable} {script}')

    first = a.ask('B', 'x', tmp_path, signature='same')
    second = a.ask('B', 'x', tmp_path, signature='same')
    assert first.payload['value'] == second.payload['value']
    assert counter.read_text() == '1', 'the agent must be asked only once'


def test_a_different_failure_is_asked_again(tmp_path: Path):
    a = agent_mod.Agent(cmd=_fake(tmp_path, json.dumps(
        {'kind': 'facts', 'value': {'preparation': {}}})))
    a.ask('B', 'x', tmp_path, signature='one')
    a.ask('B', 'x', tmp_path, signature='two')
    assert a.calls == 2


def test_the_budget_is_enforced(tmp_path: Path):
    a = agent_mod.Agent(cmd=_fake(tmp_path, json.dumps(
        {'kind': 'facts', 'value': {'preparation': {}}})), max_calls=2)
    a.ask('B', 'x', tmp_path, signature='1')
    a.ask('B', 'x', tmp_path, signature='2')
    with pytest.raises(agent_mod.AgentUnavailable, match='budget'):
        a.ask('B', 'x', tmp_path, signature='3')


def test_the_prompt_goes_on_stdin_not_argv(tmp_path: Path):
    """A fallback hands over a whole tree; argv has a hard ceiling."""
    seen = tmp_path / 'seen'
    script = tmp_path / 'echoing_agent.py'
    script.write_text(
        'import sys, pathlib\n'
        f"pathlib.Path({str(seen)!r}).write_text(sys.stdin.read())\n"
        "print('{\"kind\": \"facts\", \"value\": "
        "{\"preparation\": {}}}')\n", encoding='utf-8')
    a = agent_mod.Agent(cmd=f'{sys.executable} {script}')
    prompt = 'x' * 200000
    a.ask('A', prompt, tmp_path)
    assert seen.read_text() == prompt
