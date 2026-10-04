"""How a project's submodules are prepared, and how the check knows.

Two measured facts shaped this, and each is a way the old version was wrong:

  * **One op, not one per submodule.** `git submodule update --init
    --recursive` walks git's own roster; enumerating the roster here is a
    second copy of it, and per-submodule ops were not redundant but BROKEN.

  * **An uninitialized submodule leaves its directory BEHIND, EMPTY.** The old
    per-submodule check was `{"path": <dir>}`, so it reported "already applied"
    for exactly the state the op exists to fix: the op never ran, and the
    build then failed on a missing directory — reported as the project's
    problem. The check now asks GIT (`submodule status`: `-` uninitialized,
    `+` wrong commit, `U` conflict), and is written so the same op is correct
    for a checkout with submodules, one without, and a tree that is not a
    checkout at all.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from protocolize.onboard.extract import capture_prepare


def test_the_submodule_op_is_one_and_recursive():
    ops = capture_prepare(declared=None)
    subs = [o for o in ops if o['op'] == 'submodules']
    assert len(subs) == 1, 'submodules are ONE op, not one per submodule'
    op = subs[0]
    assert op['run'] == 'git submodule update --init --recursive', (
        '--recursive: a nested submodule is missed without it, and --init '
        'makes the command itself idempotent')
    assert isinstance(op['check'], str), (
        'the check is a shell line so it can decide the whole question')


def test_the_check_is_correct_in_all_three_settings(tmp_path: Path):
    """A checkout with submodules, one without, and not a checkout at all."""
    check = next(o['check'] for o in capture_prepare() if o['op'] == 'submodules')

    def says_done(cwd: Path) -> bool:
        return subprocess.run(check, shell=True, cwd=str(cwd)).returncode == 0

    def git(cwd: Path, *args):
        subprocess.run(['git', *args], cwd=str(cwd), capture_output=True,
                       check=False, env={'PATH': '/usr/bin:/bin',
                                         'GIT_AUTHOR_NAME': 't',
                                         'GIT_AUTHOR_EMAIL': 't@t',
                                         'GIT_COMMITTER_NAME': 't',
                                         'GIT_COMMITTER_EMAIL': 't@t',
                                         'HOME': str(tmp_path)})

    # 1. not a checkout at all -> nothing to do, and the op must NOT run
    #    (running it would exit 128).
    plain = tmp_path / 'plain'
    plain.mkdir()
    assert says_done(plain), 'no .git means there is nothing to prepare'

    # 2. a checkout with no submodules -> the command is a no-op, so done.
    bare = tmp_path / 'bare'
    bare.mkdir()
    git(bare, 'init', '-q', '.')
    assert says_done(bare), 'a no-op must not be re-run every time'

    # 3. a checkout with an UNINITIALIZED submodule -> NOT done. This is the
    #    state the old `{"path": ...}` check called "already applied".
    lib = tmp_path / 'lib'
    lib.mkdir()
    (lib / 'f.txt').write_text('x\n')
    git(lib, 'init', '-q', '.')
    git(lib, 'add', '-A')
    git(lib, 'commit', '-qm', 'init')

    main = tmp_path / 'main'
    main.mkdir()
    git(main, 'init', '-q', '.')
    subprocess.run(['git', '-c', 'protocol.file.allow=always', 'submodule',
                    'add', '-q', str(lib), 'sub/lib'], cwd=str(main),
                   capture_output=True, check=False, env={
                       'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path),
                       'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                       'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'})
    git(main, 'commit', '-qm', 'init')
    clone = tmp_path / 'clone'
    subprocess.run(['git', 'clone', '-q', str(main), str(clone)],
                   capture_output=True, check=False,
                   env={'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)})

    assert (clone / 'sub' / 'lib').is_dir(), (
        'an uninitialized submodule still leaves its directory behind — the '
        'fact that made a "path exists" check report done when nothing was')
    assert not list((clone / 'sub' / 'lib').iterdir()), 'and it is EMPTY'
    assert not says_done(clone), (
        'git says this submodule is not initialized, so the op must run')


def test_a_declared_op_keeps_its_place():
    """The agent's patches come first; the submodule op only ever appends."""
    patch = {'op': 'portability', 'check': 'grep -q fixed f',
             'run': 'sed -i s/a/b/ f'}
    ops = capture_prepare(declared=[patch])
    assert ops[0] == patch
    assert ops[-1]['op'] == 'submodules'
