"""The entry point's contract holds for a bad source, on both halves.

`fetch()` promises, in its own docstring, to give up by RAISING the reason
"rather than crashing with a traceback".  The clone half kept that promise.
The local-path half did not: a mistyped path ran `shutil.copytree` bare, so it
exited through a raw `FileNotFoundError` — a traceback where the entry point's
contract says exit 1 with a message.

Measured before the fix:

    $ python3 admit.py /tmp/does-not-exist-xyz --name zzz
    FileNotFoundError: [Errno 2] No such file or directory: '/tmp/does-not-exist-xyz'

The contract matters more now that `admit.py` is the single entry point: a
step-1 failure is a REFUSAL (exit 1), and a caller branching on the exit code
must not have to parse a traceback to learn that.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / 'skills/fetch-benchmark-project/scripts/fetch.py'


def _load_fetch():
    spec = importlib.util.spec_from_file_location('fetch_skill_badsource',
                                                  _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_missing_local_source_raises_fetch_error(tmp_path: Path):
    fetch = _load_fetch()
    with pytest.raises(fetch.FetchError) as caught:
        fetch.fetch(str(tmp_path / 'nope'), tmp_path / 'dest')
    assert 'could not copy' in str(caught.value)


def test_the_driver_refuses_it_with_exit_one(tmp_path: Path, capsys):
    """Through the driver, so the exit code and the message are both pinned."""
    import admit

    fetch = _load_fetch()
    fetch.BENCH = tmp_path / 'benchmark'
    namespace = dict(fetch.__dict__)

    rc = admit.main([str(tmp_path / 'nope'), '--name', 'zzz'],
                    namespace=namespace)
    assert rc == 1, 'a source that cannot be fetched is a REFUSAL'
    err = capsys.readouterr().err
    assert err.startswith('REFUSED:'), err
    assert 'could not copy' in err, err


def test_a_failed_fetch_leaves_no_number_behind(tmp_path: Path, capsys):
    """The empty shell `copytree` creates must not consume an admission number.

    `shutil.copytree` makes the destination BEFORE it scans the source, so a
    bad path leaves an EMPTY admission directory.  `benchmark/*` is ignored by
    git, so that shell is invisible to `git status` — but `next_number` globs
    directories, so it silently takes a number and the next real project is
    admitted under the wrong one.  Measured: four aborted invocations left
    015/016/017/018 behind, each having eaten a number.
    """
    import admit

    fetch = _load_fetch()
    bench = tmp_path / 'benchmark'
    fetch.BENCH = bench
    namespace = dict(fetch.__dict__)

    for _ in range(3):
        assert admit.main([str(tmp_path / 'nope'), '--name', 'zzz'],
                          namespace=namespace) == 1

    capsys.readouterr()
    assert list(bench.glob('[0-9][0-9][0-9]-*')) == [], (
        'a refused admission must leave the number free')
    assert namespace['next_number'](bench) == 1
