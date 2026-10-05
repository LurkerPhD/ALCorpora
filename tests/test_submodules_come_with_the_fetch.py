"""Where a checkout's submodules get fetched, and how "done" is decided.

Three measured facts shaped this:

  * **Fetching submodules is part of fetching the checkout.** `git submodule
    update --init --recursive` is idempotent and walks git's own roster, so it
    runs unconditionally in `fetch` — a tree that vendors nothing makes it a
    no-op (measured: 17 ms). It is NOT a prepare op: by the time prepare runs,
    fetch has already done it, so the op could only ever report "already done".

  * **One command, not one op per submodule.** Enumerating the roster beside
    git is a second copy of it, and per-submodule ops were not merely
    redundant but BROKEN.

  * **An uninitialized submodule leaves its directory BEHIND, EMPTY.** So a
    check of "the directory exists" reports "already applied" for exactly the
    state it exists to fix — measured, on the old per-submodule check. The
    question is therefore asked of GIT, and `git submodule status --recursive`
    answers all three tree shapes: `-` uninitialized / `+` wrong commit / `U`
    conflict means NOT done; no `.git` at all means there is nothing to do.

That last check survives as an ASSERTION on the fetch, not as a gate on an op.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from alcorpora.onboard.extract import capture_prepare

_ROOT = Path(__file__).resolve().parents[1]
_FETCH_PY = _ROOT / 'skills/fetch-benchmark-project/scripts/fetch.py'

# What "the submodules are in" means, asked of git (see the module docstring).
_SUBMODULES_DONE = ('test ! -e .git || test -z "$(git submodule status '
                    '--recursive 2>/dev/null | grep \'^[-+U]\')"')


def _load_fetch(name: str):
    spec = importlib.util.spec_from_file_location(name, _FETCH_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_GIT_ENV = {'PATH': '/usr/bin:/bin', 'GIT_AUTHOR_NAME': 't',
            'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
            'GIT_COMMITTER_EMAIL': 't@t'}


def _git(cwd: Path, *args, home: Path):
    env = dict(_GIT_ENV, HOME=str(home))
    return subprocess.run(['git', *args], cwd=str(cwd), capture_output=True,
                          check=False, env=env)


def _clone_with_uninitialized_submodule(tmp_path: Path) -> Path:
    """A real repository whose submodule is registered but NOT fetched."""
    lib = tmp_path / 'lib'
    lib.mkdir()
    (lib / 'f.txt').write_text('x\n')
    _git(lib, 'init', '-q', '.', home=tmp_path)
    _git(lib, 'add', '-A', home=tmp_path)
    _git(lib, 'commit', '-qm', 'init', home=tmp_path)

    main = tmp_path / 'main'
    main.mkdir()
    _git(main, 'init', '-q', '.', home=tmp_path)
    env = dict(_GIT_ENV, HOME=str(tmp_path), GIT_ALLOW_PROTOCOL='file')
    subprocess.run(['git', '-c', 'protocol.file.allow=always', 'submodule',
                    'add', '-q', str(lib), 'sub/lib'], cwd=str(main),
                   capture_output=True, check=False, env=env)
    _git(main, 'commit', '-qm', 'init', home=tmp_path)
    clone = tmp_path / 'clone'
    subprocess.run(['git', 'clone', '-q', str(main), str(clone)],
                   capture_output=True, check=False,
                   env={'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)})
    return clone


def test_fetch_brings_submodules_in(tmp_path: Path):
    """`fetch` leaves the submodules IN — the point of doing it there."""
    fetch = _load_fetch('fetch_skill_submod')
    clone = _clone_with_uninitialized_submodule(tmp_path)
    assert not list((clone / 'sub' / 'lib').iterdir()), 'starts uninitialized'
    # A local submodule URL needs git's file protocol allowed (git >= 2.38).
    # Injected through git's env-config because `fetch` spawns the command with
    # the REAL environment — a fake HOME would never reach it. Real trees have
    # real URLs and need none of this.
    os.environ['GIT_CONFIG_COUNT'] = '1'
    os.environ['GIT_CONFIG_KEY_0'] = 'protocol.file.allow'
    os.environ['GIT_CONFIG_VALUE_0'] = 'always'
    try:
        # A COPY is the local-path branch of `fetch`, which is what the fixture
        # is: it must bring the submodules in just as a clone does.
        dest = tmp_path / 'copied'
        fetch.fetch(str(clone), dest)
        assert list((dest / 'sub' / 'lib').iterdir()), (
            'fetch did not bring the submodule in')
    finally:
        for k in ('GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0',
                  'GIT_CONFIG_VALUE_0'):
            os.environ.pop(k, None)


def test_native_leaves_submodules_alone(tmp_path: Path):
    """The build step neither fetches nor needs to — fetching already did.

    Pinned so the command does not drift back into the build step: two places
    running it is one place too many, and the build step is the wrong one.
    """
    fetch = _load_fetch('fetch_skill_submod2')
    ran: list = []
    fetch.run = lambda line, cwd: (ran.append(line), (True, ''))[1]
    args = SimpleNamespace(prepare_ops=[], configure='c', build='b',
                           test='t', tree_subdir='.')
    src = tmp_path / 'tree'
    src.mkdir()
    fetch.native(src, args)
    assert not any('submodule' in c for c in ran), (
        'submodule fetching belongs to `fetch`, not to the build')


def test_the_git_check_answers_all_three_tree_shapes(tmp_path: Path):
    """The post-condition is decidable wherever the fetch was pointed at."""
    def says_done(cwd: Path) -> bool:
        return subprocess.run(_SUBMODULES_DONE, shell=True,
                              cwd=str(cwd)).returncode == 0

    # 1. not a checkout at all -> nothing to fetch, and running the command
    #    there would exit 128.
    plain = tmp_path / 'plain'
    plain.mkdir()
    assert says_done(plain), 'no .git means there is nothing to fetch'

    # 2. a checkout with no submodules -> the command is a no-op.
    bare = tmp_path / 'bare'
    bare.mkdir()
    _git(bare, 'init', '-q', '.', home=tmp_path)
    assert says_done(bare), 'a no-op must not read as missing'

    # 3. a checkout with an UNINITIALIZED submodule -> NOT done. This is the
    #    state a `{"path": ...}` check called "already applied".
    clone = _clone_with_uninitialized_submodule(tmp_path)
    assert (clone / 'sub' / 'lib').is_dir(), (
        'an uninitialized submodule still leaves its directory behind — the '
        'fact that made a "path exists" check report done when nothing was')
    assert not list((clone / 'sub' / 'lib').iterdir()), 'and it is EMPTY'
    assert not says_done(clone), 'git says this submodule is not initialized'


def test_prepare_carries_only_what_was_declared():
    """Nothing is DERIVED into `prepare` — least of all the submodule fetch.

    A prepare op is a portability patch, a delta between the shipped source and
    a buildable one that nothing in the shipped source states. Submodules are
    not that: they are part of the checkout, and the fetch already ran.
    """
    assert capture_prepare(declared=None) == []
    patch = {'op': 'patch', 'check': 'grep -q fixed f',
             'run': 'sed -i s/a/b/ f'}
    ops = capture_prepare(declared=[patch])
    assert ops == [patch], 'the declared ops, in order, and nothing appended'
    assert ops[0] is not patch, 'copied, so a caller cannot mutate the source'


class _Spy:
    """Records every command `fetch` runs, then really runs it."""

    def __init__(self, real):
        self._real = real
        self.calls: list = []

    def run(self, *a, **k):
        argv = a[0] if a else k.get('args')
        self.calls.append(list(argv) if isinstance(argv, (list, tuple))
                          else argv)
        return self._real.run(*a, **k)

    def updates(self) -> list:
        return [c for c in self.calls
                if isinstance(c, list) and 'update' in c and 'submodule' in c]


def test_a_tree_with_nothing_vendored_never_runs_the_update(tmp_path: Path):
    """It is CHECKED, and the check is what decides — not a blind command."""
    fetch = _load_fetch('fetch_skill_submod_none')
    plain = tmp_path / 'plain'
    plain.mkdir()
    (plain / 'a.txt').write_text('x\n')
    _git(plain, 'init', '-q', '.', home=tmp_path)

    spy = _Spy(subprocess)
    fetch.subprocess = spy
    fetch.fetch(str(plain), tmp_path / 'out')
    assert spy.updates() == [], (
        'a checkout with no submodules needs no fetch, so none may run')


def test_a_submodule_that_never_arrives_is_retried_then_raises(tmp_path: Path):
    """Retrying is the point; handing the build an empty directory is not.

    A partial fetch over a flaky link leaves the rest outstanding, so the
    check is repeated after each attempt. When it never settles the fetch
    FAILS, naming what is missing — the build would otherwise report a
    missing header from a directory that was never filled.
    """
    fetch = _load_fetch('fetch_skill_submod_retry')
    clone = _clone_with_uninitialized_submodule(tmp_path)
    # Break the submodule's URL by removing what it points at: the fetch then
    # cannot settle, which is the state the retry loop exists for.
    import shutil as _shutil
    _shutil.rmtree(tmp_path / 'lib')

    spy = _Spy(subprocess)
    fetch.subprocess = spy
    with pytest.raises(fetch.FetchError) as caught:
        fetch.fetch(str(clone), tmp_path / 'out', retries=2)
    assert len(spy.updates()) == 2, 'the update is retried, not run once'
    assert 'sub/lib' in str(caught.value), (
        'the failure names the submodule that did not arrive')
