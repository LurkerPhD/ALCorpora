"""Calling an agent at a seam, and treating what it says as data.

The flow asks a question, gets a typed answer, and VALIDATES it. Nothing else
about the agent matters: it is a library function that happens to be a
language model, and like any other call it can fail, time out, or return
something malformed.

Two kinds of failure, kept apart on purpose:

* **The agent could not be reached** — no command, a crash, a timeout — is
  `AgentUnavailable`. It says nothing about the project, so the flow exits
  with its own status and a reader knows to look at the infrastructure.
  Measured reasoning: an operator who sees "the project was refused" when the
  truth is "`claude` is not on PATH" will research the wrong thing.

* **The agent answered, and the answer is unusable** — bad JSON, a missing
  `kind`, an incomplete payload — is a seam failure. It is retried within the
  seam's own budget, and it is the agent's answer that failed, not the call.

Every answer is written to disk before it is judged, so a refusal can be read
back rather than re-derived. And answers are CACHED by
`(seam, failure signature)`: the same failure must not produce a different
answer on a second run, or the corpus stops being comparable with itself —
that is the whole point of having one.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import seams


class AgentUnavailable(RuntimeError):
    """The agent could not be run.  Never a verdict about a project."""


class SeamFailed(RuntimeError):
    """The agent answered, and the answer could not be used.

    Carries the raw answer: a refusal that shows only the parsed form hides
    the shape the answer actually had, which is what a reader needs to see.
    """

    def __init__(self, message: str, raw: str = '') -> None:
        super().__init__(message)
        self.raw = raw


@dataclass
class Agent:
    """One configured way to ask.  *cmd* is overridable so tests need no LLM."""

    cmd: str = 'claude'
    timeout_s: int = 900
    log_dir: Path | None = None
    max_calls: int = 6
    calls: int = 0
    cache: dict = field(default_factory=dict)

    def available(self) -> bool:
        return bool(self.cmd and shlex.split(self.cmd))

    def ask(self, seam: str, prompt: str, cwd: Path,
            *, signature: str = '') -> seams.Response:
        """Ask at *seam*.  Raises `AgentUnavailable` or `SeamFailed`.

        *signature* identifies the failure this question is about.  With it,
        a repeated run over the same failure returns the SAME answer instead
        of a fresh one — see the module docstring.
        """
        if not self.available():
            raise AgentUnavailable(
                'no agent command: pass --agent-cmd or set '
                'ALCORPORA_AGENT_CMD')
        key = (seam, signature)
        if key in self.cache:
            return self.cache[key]
        if self.calls >= self.max_calls:
            raise AgentUnavailable(
                f'the agent budget is spent ({self.max_calls} calls): the '
                f'flow will not keep asking')

        argv = shlex.split(self.cmd) + ['-p', '--output-format', 'json']
        self.calls += 1
        try:
            # The prompt goes on STDIN, never in argv: a fallback hands over
            # the whole project tree, and argv has a hard per-argument and
            # total ceiling that a real repository blows through.
            proc = subprocess.run(argv, input=prompt, cwd=str(cwd),
                                  capture_output=True, text=True,
                                  timeout=self.timeout_s,
                                  env=None)
        except FileNotFoundError as exc:
            raise AgentUnavailable(f'{argv[0]} is not runnable: {exc}') from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentUnavailable(
                f'the agent did not answer within {self.timeout_s}s') from exc

        # WHY stdout ALONE, measured on the real `claude -p --output-format
        # json`: the envelope goes to stdout, and everything else it prints —
        # a warning, a progress line — goes to stderr.  So stdout is the
        # answer, unentangled.  On this machine it also emits
        # `[claude-code:unrecognized_model] ...` on stderr, which would break
        # `json.loads` if the two were merged.
        out = proc.stdout or ''
        self._save(seam, signature, out, proc.stderr or '', proc.returncode)
        if proc.returncode != 0:
            raise AgentUnavailable(
                f'{argv[0]} exited {proc.returncode}: '
                f'{(proc.stderr or proc.stdout or "").strip()[:400]}')
        try:
            resp = seams.parse(out)
        except seams.SeamError as exc:
            raise SeamFailed(str(exc), raw=out) from exc
        bad = seams.problems(resp)
        if bad:
            raise SeamFailed('; '.join(bad), raw=out)
        self.cache[key] = resp
        return resp

    #: The raw answer is kept even when it is unusable: that is the artifact a
    #: reader needs when a seam fails, and keeping only the parsed form hides
    #: the shape it actually had.
    def _save(self, seam: str, signature: str, out: str, err: str,
              rc: int) -> None:
        if self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(
            f'{seam}\0{signature}'.encode('utf-8')).hexdigest()[:16]
        path = self.log_dir / f'{self.calls:02d}-{seam}-{digest}.json'
        path.write_text(json.dumps(
            {'seam': seam, 'signature': signature, 'returncode': rc,
             'stdout': out, 'stderr': err}, indent=1), encoding='utf-8')
