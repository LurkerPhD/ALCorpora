"""The declaration a project makes about its own correctness must reach the
capture.

Measured while admitting a real project: `fetch.py` called
`capture_verification` with no `facts`, so the plan it wrote staged nothing.
The project's programs need a home directory, a resource directory and a
program config before they will run at all — without them each program exits
0 having written nothing, every case reports "no output", and the failure
reads as a broken project rather than as a declaration that was never
passed.  The same call also resolved the reference run's target against the
benchmark folder instead of the tree the build lives in.

Both are pinned here by observing the CALL, because what went wrong was
which arguments were handed over — a plan that merely loads does not show it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / 'skills/fetch-benchmark-project/scripts/fetch.py'


def _load_fetch():
    spec = importlib.util.spec_from_file_location('fetch_skill', _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: A plan with nothing to run, so `prove_legal` walks straight through §B
#: and the assertions below are about the call, not about a verdict.
_PLAN = {
    'plan_version': 1,
    'target': {'kind': 'product', 'product': 'p', 'dirs': []},
    'protocol': 'protocol.json',
    'cases': {'kind': 'one_per_dir', 'root': '${TREE}/v', 'exclude': [],
              'id': {'from': 'dirname'},
              'input': {'name': '{id}.in', 'id_spelling': {}},
              'skip_without_input': False},
    'inputs': {'from': 'case_dir', 'exclude_suffix': []},
    'staging': [],
    'run': {'kind': 'process', 'argv': ['{target}'], 'cwd': '{work}',
            'exit_accept': [0], 'case_timeout_s': 5},
    'produced': [],
    # A stamp the origin would carry; the plan demands a non-empty one, and
    # the run itself is mocked below, so its VALUE is not under test here.
    'golden': {'source': 'none', 'root': '{origin}', 'path': '',
               'stamp': 'STAMP'},
    'assertions': [{'kind': 'artifact', 'path': '{work}', 'exists': True}],
    'chains': {},
    'report': {'repeats': 1},
    'summary': {'require_zero': ['crash'], 'require_min': {'pass': 1}},
    'provenance': '',
}


def test_the_projects_declaration_reaches_the_capture(tmp_path: Path):
    """Driven through the entry point, because RECORDING moved to the driver.

    `prove_legal` used to write the row itself. It no longer does: a caller
    that can open a seam may repair a failure and reach a verdict, so a row
    written inside the mechanical half would report the intermediate failure
    as the outcome. The driver records; this test drives the driver.
    """
    import admit

    fetch = _load_fetch()
    dest = tmp_path / '003-thing'
    src = dest / 'thing'
    src.mkdir(parents=True)
    # One found beside the source, one named on the command line: both routes
    # have to arrive.
    (src / 'facts.json').write_text('{"preparation": {}}', encoding='utf-8')
    (src / 'rules.json').write_text('{}', encoding='utf-8')

    seen: dict = {}

    # `record_row` writes the benchmark's own CSV.  A test that let it would
    # EDIT the corpus it is testing — measured: the row for 003 was replaced
    # by this fixture's, and every later read of the benchmark reported a
    # project that does not exist.
    csv = tmp_path / 'benchmark.csv'

    def fake_capture(proto, build, tree_root, **kw):
        seen.update(kw)
        return dict(_PLAN)

    class _Proto:
        compilation: list = []
        prepare: list = []
        products: list = []

        def save(self, path):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('[]', encoding='utf-8')

    fetch.BENCH_CSV = csv
    fetch.native = lambda s, a: (True, '', [])

    with mock.patch('alcorpora.onboard.extract.extract_and_prove',
                    return_value=(_Proto(), mock.Mock(compared=0))), \
            mock.patch('alcorpora.onboard.extract.capture_verification',
                       side_effect=fake_capture), \
            mock.patch('alcorpora.verify.cli.main', return_value=0):
        rc = admit.main([str(dest), '--readmit', '--agent-cmd', 'false',
                         '--declaration', 'rules.json'],
                        namespace=fetch.__dict__)

    assert rc == 0, 'the walk-through must finish'
    assert seen.get('facts') == src / 'facts.json', (
        f'the project\'s facts never reached the capture: {seen.get("facts")}')
    assert seen.get('declaration') == src / 'rules.json', (
        f'the comparison rules never reached the capture: '
        f'{seen.get("declaration")}')
    assert csv.is_file() and 'thing' in csv.read_text(encoding='utf-8'), (
        'the run must record its own row — into the CSV `record_row` was '
        'given, not into the benchmark\'s')


def test_a_declaration_is_resolved_against_the_project(tmp_path: Path):
    fetch = _load_fetch()
    src = tmp_path / 'checkout'
    assert fetch._declared(None, src) is None
    assert fetch._declared('facts.json', src) == src / 'facts.json'
    absolute = tmp_path / 'elsewhere.json'
    assert fetch._declared(str(absolute), src) == absolute
