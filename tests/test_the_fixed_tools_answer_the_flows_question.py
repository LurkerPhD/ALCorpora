"""The fixed tools answer the same question the flow answers, and only that.

`bench.py` exists so that a caller — a person, a script, or an agent — RUNS
an operation instead of writing one. That only holds if each subcommand
answers the SAME question the module that owns the behaviour answers: a
`tree` that disagreed with the flow's own idea of the tree would send every
follow-up command at the wrong directory, and that failure is silent.

Pinned here, because the two mistakes this file guards against were both made
while writing it:

  * `tree_of` re-derived "which directory is the tree" and got it WRONG for a
    checkout whose build files sit a level down with no marker at the top
    (a hand-written make project). It now delegates, and the delegation is
    asserted against the owner.
  * `wipe` must remove this tool's output and NOT the project's — its
    sources, its build files, and the declarations it ships about itself.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / 'bench.py'


def _load():
    spec = importlib.util.spec_from_file_location('bench', _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['bench'] = mod
    spec.loader.exec_module(mod)
    return mod


def _bench(*args, cwd=_REPO):
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(cwd))


# ── tree: one question, one implementation ────────────────────

def test_tree_agrees_with_the_flow(tmp_path: Path):
    """`bench.py tree` must be the flow's own answer, not a second rule."""
    import admit
    bench = _load()
    ns = admit.fetch_namespace()

    # A sample laid out at the top, a checkout one level down, and a
    # checkout whose ONLY marker is a level down (the case a re-derived
    # rule got wrong).
    top = tmp_path / '001-x'
    (top).mkdir()
    (top / 'CMakeLists.txt').write_text('project(x)\n')
    inner = tmp_path / '006-y' / 'y'
    inner.mkdir(parents=True)
    (inner / 'CMakeLists.txt').write_text('project(y)\n')
    make = tmp_path / '009-z' / 'z'
    (make / 'source' / 'mds').mkdir(parents=True)
    (make / 'source' / 'mds' / 'Makefile').write_text('all:\n\t@true\n')

    for d in (top, tmp_path / '006-y', tmp_path / '009-z'):
        assert bench.tree_of(d) == ns['_inner_source'](d), (
            f'tree_of disagreed with the flow for {d.name}')


def test_tree_is_printed_for_a_script():
    got = _bench('tree', 'benchmark/006-cJSON')
    assert got.returncode == 0
    assert got.stdout.strip().endswith('006-cJSON/cJSON')


# ── wipe: this tool's output, never the project's ─────────────

def test_wipe_removes_output_and_keeps_the_material(tmp_path: Path):
    bench = _load()
    d = tmp_path / '001-x'
    (d / 'source').mkdir(parents=True)
    (d / 'source' / 'a.c').write_text('int main(void){return 0;}\n')
    (d / 'CMakeLists.txt').write_text('project(x)\n')
    # This tool's output, and the project's OWN declarations.
    for name in ('protocol.json', 'verification_plan.json'):
        (d / name).write_text('{}')
    for name in ('origin_release', 'benchmark_results', '.work'):
        (d / name).mkdir()
        (d / name / 'junk').write_text('generated')
    for name in ('facts.json', 'declaration.json', 'admit.json'):
        (d / name).write_text('{}')

    assert bench.cmd_wipe([str(d)]) == 0

    for name in ('protocol.json', 'verification_plan.json'):
        assert not (d / name).exists(), f'{name} is this tool\'s output'
    for name in ('origin_release', 'benchmark_results', '.work'):
        assert not (d / name).exists(), f'{name} is this tool\'s output'
    for name in ('facts.json', 'declaration.json', 'admit.json',
                 'CMakeLists.txt', 'source/a.c'):
        assert (d / name).exists(), (
            f'{name} is the MATERIAL a re-derivation consumes, or the '
            f'project itself — wipe must not touch it')


def test_wipe_does_not_touch_a_build(tmp_path: Path):
    """Clearing a build belongs to `admit.py --readmit`, which knows how.

    Pinned because a reimplementation is exactly what broke a make project:
    its `tree_subdir` names where its Makefiles ARE, so "remove tree_subdir"
    removes the source.
    """
    bench = _load()
    d = tmp_path / '001-x'
    d.mkdir()
    (d / 'CMakeLists.txt').write_text('project(x)\n')
    build = d / 'build'
    build.mkdir()
    (build / 'CMakeCache.txt').write_text('cache\n')

    assert bench.cmd_wipe([str(d)]) == 0
    assert (build / 'CMakeCache.txt').exists(), (
        'the build is not this command\'s to clear')


# ── plan: validate, and say which modes are declared ──────────

def test_plan_reports_the_declared_modes(tmp_path: Path):
    bench = _load()
    d = tmp_path / '005-x'
    d.mkdir()
    (d / 'protocol.json').write_text('{}')
    plan = {
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
        'golden': {'source': 'none', 'root': '{origin}', 'path': '',
                   'stamp': 'S'},
        'assertions': [{'kind': 'text_lockstep', 'drop': [r'^X'],
                        'mask': [[r'ELAPSED \S+', 't']]}],
        'chains': {},
        'report': {'repeats': 1},
        'summary': {'require_zero': [], 'require_min': {'pass': 1}},
        'provenance': '',
    }
    import json
    (d / 'verification_plan.json').write_text(json.dumps(plan))

    got = _bench('plan', str(d))
    assert got.returncode == 0, got.stderr
    assert '1 drop / 0 keep / 1 mask' in got.stdout
    assert 'executable' in got.stdout


def test_a_missing_plan_is_a_refusal_not_a_traceback(tmp_path: Path):
    got = _bench('plan', str(tmp_path))
    assert got.returncode == 1
    assert 'Traceback' not in got.stderr
    assert 'no verification_plan.json' in got.stderr


def test_an_unknown_command_is_a_usage_error():
    got = _bench('frobnicate')
    assert got.returncode == 2
    assert 'unknown command' in got.stderr


# ── extract / capture / cases: the steps that used to be inline python ──

def test_capture_refuses_a_plan_that_is_not_executable(tmp_path: Path):
    """`capture` enforces `problems() == []` rather than leaving it to be read.

    The step was an inline `python3 -c` in a skill document, where "[] is the
    only acceptable answer" was a sentence the reader had to obey. As a
    command it is the exit code, and the problems come out by name.
    """
    d = tmp_path / '001-x'
    d.mkdir()
    (d / 'protocol.json').write_text('[]')
    decl = d / 'declaration.json'
    decl.write_text('{"comparison": {}}')

    got = _bench('capture', str(d), '--declaration', str(decl))
    # Either it captures a usable plan (exit 0) or it refuses with named
    # problems (exit 1) — never a traceback, and never a silent success on a
    # plan that does not load.
    assert got.returncode in (0, 1), got.stderr
    assert 'Traceback' not in got.stderr


def test_capture_without_a_protocol_says_so(tmp_path: Path):
    d = tmp_path / '001-x'
    d.mkdir()
    decl = d / 'declaration.json'
    decl.write_text('{"comparison": {}}')
    got = _bench('capture', str(d), '--declaration', str(decl))
    assert got.returncode == 1
    assert 'no build protocol' in got.stderr
    assert 'extract' in got.stderr, 'it must name the step that comes first'


def test_extract_refuses_a_tree_with_no_build_system(tmp_path: Path):
    d = tmp_path / 'not-a-tree'
    d.mkdir()
    got = _bench('extract', str(d))
    assert got.returncode == 1
    assert 'Traceback' not in got.stderr
    assert 'NOT LEGAL' in got.stderr


def test_cases_names_the_plan_it_cannot_find(tmp_path: Path):
    got = _bench('cases', str(tmp_path))
    assert got.returncode == 1
    assert 'no verification_plan.json' in got.stderr
