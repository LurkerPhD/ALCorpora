"""Logging: tee, timestamps, colors, and the UNIFIED action API.

Every user-facing tagged line in scripts_v2 goes through ``log.action()``,
the single output interface, so the format is uniform:

    {indent}{ts()} [{TAG}] {message}

TAG is one fixed :class:`Action` enum member, colored by the fixed
``ACTION_COLORS`` mapping.  Level actions (INFO/OK/WARN/FAIL/ERROR) render
plain; lifecycle marker actions (AGENT/COMMIT/...) render bold.  Raw
subprocess output uses ``raw()`` (no decoration).  Default indent=1
(4 spaces) for module output; entry code may pass indent=0.
"""
from __future__ import annotations

from enum import Enum
import os
import sys
import time
from pathlib import Path

# ── Colors ────────────────────────────────────────────────────
C = {
    'green': '\033[32m', 'red': '\033[31m', 'yellow': '\033[33m',
    'blue': '\033[34m', 'magenta': '\033[35m',
    'cyan': '\033[36m', 'bold': '\033[1m', 'reset': '\033[0m',
}


def color(text: str, name: str) -> str:
    c = C.get(name, '')
    return f"{c}{text}{C['reset']}" if c else text


# ── Tee ───────────────────────────────────────────────────────
class _Tee:
    """Duplicate writes to both the original stream and a log file."""

    def __init__(self, stream, log_file):
        self.stream = stream
        self.log_file = log_file

    def write(self, text):
        self.stream.write(text)
        self.log_file.write(text)
        self.log_file.flush()

    def flush(self):
        self.stream.flush()
        self.log_file.flush()


def install_tee(log_path: Path) -> None:
    """Tee stdout/stderr to *log_path* — including subprocess output.

    Replacing only ``sys.stdout``/``sys.stderr`` (the Python objects) is
    not enough: child processes (the ``tdd_loop`` subprocess spawned by
    ``edd_loop``) inherit the real fds 1/2, so their output bypasses the
    tee and is lost — the "FAIL line missing from the log" bug.  Redirect
    the underlying fds to the log file too, keeping a duplicate of the
    originals as the tee's console leg so the operator still sees output.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lf = log_path.open('w', encoding='utf-8', buffering=1)

    # Console leg: duplicate the real fds BEFORE they are redirected, so
    # the tee can still write to the terminal.
    console_out = os.fdopen(os.dup(sys.stdout.fileno()), 'w',
                            encoding='utf-8', errors='replace', buffering=1)
    console_err = os.fdopen(os.dup(sys.stderr.fileno()), 'w',
                            encoding='utf-8', errors='replace', buffering=1)

    # Point fds 1/2 at the log file so subprocess output lands there too.
    os.dup2(lf.fileno(), sys.stdout.fileno())
    os.dup2(lf.fileno(), sys.stderr.fileno())

    sys.stdout = _Tee(console_out, lf)
    sys.stderr = _Tee(console_err, lf)


# ── Timestamps / elapsed ──────────────────────────────────────
_START_TS = time.monotonic()
_STEP_TS = time.monotonic()

# When spawned by an EDD run (edd_loop / find_bad_apples / full_cpp_sweep
# set EDD_ELAPSED before forking tdd_loop), inherit the parent's elapsed
# clock so the subprocess's output aligns with the global timeline; fall
# back to this process's own start when the variable is absent.
_env_elapsed = os.environ.get('EDD_ELAPSED')
if _env_elapsed:
    try:
        _START_TS = time.monotonic() - float(_env_elapsed)
    except ValueError:
        pass


def elapsed() -> float:
    """Seconds since the start clock — for cross-process inheritance."""
    return time.monotonic() - _START_TS


def reset_ts() -> None:
    global _START_TS, _STEP_TS
    now = time.monotonic()
    _START_TS = now
    _STEP_TS = now


def reset_step_ts() -> None:
    global _STEP_TS
    _STEP_TS = time.monotonic()


def ts(step_override: float | None = None) -> str:
    """Combined timestamp + elapsed: ``[HH:MM:SS +step(total)]``.

    ``+step`` is the elapsed time since the previous output line (or the
    last ``reset_step_ts()``) — it re-arms on every call, so each line
    shows the delta since the last one.  ``(total)`` keeps accumulating
    from ``reset_ts()``.  A *step_override* (e.g. a benchmark's own
    elapsed) is used verbatim and does NOT re-arm the delta clock — it is
    an independent duration, not an output interval.
    """
    global _STEP_TS
    now = time.monotonic()
    if step_override is not None:
        step = step_override
    else:
        step = now - _STEP_TS
        # Re-arm the delta clock so the next line shows its own interval.
        _STEP_TS = now
    total = now - _START_TS
    return f"[{time.strftime('%H:%M:%S')} +{step:.1f}({total:.1f})]"


def now_str() -> str:
    """Data-file timestamp: ``'%Y-%m-%d %H:%M:%S'``.

    Single source for state/yml writers (f2c_state.json ``updated``,
    todo_v2.yml header / entry ``created``, bisect checkpoint).  External
    scripts import this via the same try/except fallback as ``ts()``.
    """
    return time.strftime('%Y-%m-%d %H:%M:%S')


def file_ts() -> str:
    """Log filename timestamp: ``'%Y%m%d_%H%M%S'``.

    Single source for the agent-output log names
    (``tdd_log/tdd_<ts>_<stem>.log`` / ``edd_log/edd_<ts>_<stem>.log``).
    """
    return time.strftime('%Y%m%d_%H%M%S')


# ── Unified output API ────────────────────────────────────────
_INDENT = '    '


class Action(Enum):
    """Fixed set of tagged output actions for :func:`action`.

    Every user-facing line is one of these members; each maps to a fixed
    tag color in ``ACTION_COLORS``.
    """
    INFO = 'INFO'       # informational line
    OK = 'OK'           # success line
    WARN = 'WARN'       # warning line
    FAIL = 'FAIL'       # failure line
    ERROR = 'ERROR'     # error line
    ACTION = 'ACTION'   # generic high-contrast marker
    AGENT = 'AGENT'     # invoking an agent subprocess
    COMMIT = 'COMMIT'   # git commit
    PURGE = 'PURGE'     # deleting a build dir / stale output
    F2C = 'F2C'         # f2c pipeline (re)conversion
    BUILD = 'BUILD'     # build / copy step
    BATCH = 'BATCH'     # bisect batch header
    VERIFY = 'VERIFY'   # pass/fail verdict line
    STEM = 'STEM'       # per-stem progress marker
    SUMMARY = 'SUMMARY'  # end-of-run summary


ACTION_COLORS: dict[Action, str] = {
    Action.INFO: 'cyan',
    Action.OK: 'green',
    Action.WARN: 'yellow',
    Action.FAIL: 'red',
    Action.ERROR: 'red',
    Action.ACTION: 'magenta',
    Action.AGENT: 'magenta',
    Action.COMMIT: 'green',
    Action.PURGE: 'yellow',
    Action.F2C: 'cyan',
    Action.BUILD: 'blue',
    Action.BATCH: 'blue',
    Action.VERIFY: 'magenta',
    Action.STEM: 'magenta',
    Action.SUMMARY: 'green',
}


# Lifecycle markers render bold tag + bold message; level lines do not.
_MARKER_ACTIONS = frozenset({
    Action.ACTION, Action.AGENT, Action.COMMIT, Action.PURGE, Action.F2C,
    Action.BUILD, Action.BATCH, Action.VERIFY, Action.STEM, Action.SUMMARY,
})


def action(msg: str, indent: int = 1,
           action: Action = Action.INFO) -> None:
    """Unified output line: ``{indent}{ts} [{TAG}] {msg}``.

    The single interface for every tagged line in scripts_v2.  TAG is the
    action's enum name, colored by the fixed ``ACTION_COLORS`` mapping;
    lifecycle markers (AGENT/COMMIT/...) render bold tag + bold message.
    A non-:class:`Action` value fails loudly (KeyError) instead of
    rendering an unlisted tag.
    """
    color_name = ACTION_COLORS[action]  # unknown action fails loudly here
    tag_c = color(action.value, color_name)
    if action in _MARKER_ACTIONS:
        tag_c = C['bold'] + tag_c
        msg = f"{C['bold']}{msg}{C['reset']}"
    prefix = _INDENT * indent
    print(f"{prefix}{ts()} [{tag_c}] {msg}")


def raw(msg: str) -> None:
    """Undecorated passthrough (subprocess output, progress lines)."""
    print(msg)


def step_header(title: str) -> None:
    """Compatibility alias for :func:`step` (older modules)."""
    print(f"  {'─' * 56}")
    print(f"  [{title}]")


# ── Log files ─────────────────────────────────────────────────
def write_log(path: Path, content: str) -> None:
    """Write *content* to *path*, prefixed with a timestamp header."""
    ts_now = time.strftime('%Y-%m-%d %H:%M:%S')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Log created: {ts_now}\n{content}")
