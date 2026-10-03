"""A prepare op runs AT MOST ONCE, and its own check is what decides.

The `prepare` section exists so the repeated builds and runs that follow an
onboarding do not pay the setup each time: the submodule is fetched, the patch
lands, and every later run skips because the corpus's own check holds.  These
pin the two ways that could be wrong:

* a check that is not consulted -- the op runs every time, which is the thing
  the section was added to stop;
* a check whose RESULT is ignored -- `exists: false` (a marker a patch writes)
  must skip when the marker is PRESENT, not only when a path is absent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ONBOARD_PY = (Path(__file__).resolve().parents[1] / 'scripts' / 'onboard.py')


def _load_onboard():
    spec = importlib.util.spec_from_file_location('_onboard_under_test',
                                                  ONBOARD_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Proto:
    def __init__(self, prepare):
        self.prepare = prepare


def test_an_op_whose_check_holds_does_not_run(tmp_path):
    """The whole point: the submodule is there, so nothing is fetched."""
    mod = _load_onboard()
    (tmp_path / 'submodules' / 'vendor').mkdir(parents=True)
    marker = tmp_path / 'ran'
    op = {'op': 'submodule', 'check': {'path': 'submodules/vendor'},
          'run': f'touch {marker}'}
    assert mod._run_prepare(_Proto([op]), tmp_path) == 0
    assert not marker.exists(), 'the op ran although its check held'


def test_an_op_whose_check_fails_runs(tmp_path):
    mod = _load_onboard()
    marker = tmp_path / 'ran'
    op = {'op': 'submodule', 'check': {'path': 'submodules/missing'},
          'run': f'touch {marker}'}
    assert mod._run_prepare(_Proto([op]), tmp_path) == 0
    assert marker.exists(), 'the op was skipped although its check failed'


def test_exists_false_means_done_when_the_path_is_gone(tmp_path):
    """`exists: false` is a check satisfied by absence -- a stale file a
    setup step removes.  Present means NOT done, so it runs; gone means done,
    so a repeat is skipped."""
    mod = _load_onboard()
    stale = tmp_path / 'stale'
    stale.write_text('x')
    assert mod._run_prepare(
        _Proto([{'op': 'patch', 'check': {'path': 'stale', 'exists': False},
                 'run': f'rm {stale}'}]), tmp_path) == 0
    assert not stale.exists()
    # Now absent: the check holds, so the second op must NOT run.
    assert mod._run_prepare(
        _Proto([{'op': 'patch', 'check': {'path': 'stale', 'exists': False},
                 'run': 'exit 7'}]), tmp_path) == 0


def test_a_failing_op_stops_the_run(tmp_path):
    """Half a prepared tree builds into a failure far from its cause."""
    mod = _load_onboard()
    op = {'op': 'patch', 'check': {'path': 'nope'}, 'run': 'exit 3'}
    assert mod._run_prepare(_Proto([op]), tmp_path) == 3


def test_the_build_declarations_do_not_come_from_a_corpus_config():
    """Independence: the same tree must onboard the same way twice.

    The policy flags are deepf2c-wide, so the skill declares them.  Reading
    them from the flow's `f2c_<corpus>.json` made the protocol depend on a
    file the flow owns -- measured, the same tree onboarded with the config
    present carried `['-falign-functions=64', '-DF2C_NO_BLAS']` and without
    it carried `[]`, and the second looked complete.
    """
    mod = _load_onboard()
    assert mod.DEFAULT_POLICY_FLAGS == ('-falign-functions=64', '-DF2C_NO_BLAS')
    # The parser must not have a knob that reads the config's build keys.
    src = ONBOARD_PY.read_text()
    assert "cfg.get('policy_flags'" not in src
    assert 'cfg.get("policy_flags"' not in src
