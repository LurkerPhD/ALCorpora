"""A project's own declaration is used, not silently passed over.

Measured, and the reason this is a correctness fix rather than a convenience:
re-proving a minimal sample WITHOUT `--facts` replaced its `field_assertion` —
the per-column model the project declares — with a generic line-by-line
`text_lockstep`, and the row still read PASS.

A weaker plan that passes is indistinguishable from the real one. That is
exactly what the acceptance criteria's "semantically equivalent" clause exists
to catch, and nothing in the flow said a word about it.

The file lives beside the project's own source, which is where the skill's
instructions put it, so the entry point looks there rather than requiring
every caller to remember a flag. This is NOT the widened search the capture
refuses: that rule exists because a search that widens until it finds
something finds a NEIGHBOUR's declaration. One fixed name at one fixed place
is not that.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import admit

_REPO = Path(__file__).resolve().parents[1]


def _fetch_namespace():
    spec = importlib.util.spec_from_file_location(
        'fetch_skill_inputs',
        _REPO / 'skills/fetch-benchmark-project/scripts/fetch.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.__dict__)


def test_an_explicit_path_wins(tmp_path: Path):
    src = tmp_path / 'proj'
    src.mkdir()
    (src / 'facts.json').write_text('{}')
    explicit = tmp_path / 'other.json'
    explicit.write_text('{}')
    got = admit._project_input(_fetch_namespace(), src, str(explicit),
                               'facts.json')
    assert got == explicit


def test_the_file_beside_the_source_is_used(tmp_path: Path):
    src = tmp_path / 'proj'
    src.mkdir()
    (src / 'facts.json').write_text('{}')
    got = admit._project_input(_fetch_namespace(), src, None, 'facts.json')
    assert got == src / 'facts.json'


def test_absent_means_absent(tmp_path: Path):
    src = tmp_path / 'proj'
    src.mkdir()
    assert admit._project_input(_fetch_namespace(), src, None,
                                'facts.json') is None


# ── why it matters: the plan changes shape ────────────────────

_PROTO = None


def _proto():
    global _PROTO
    if _PROTO is None:
        from protocolize.private_protocol import Product, Protocol
        _PROTO = Protocol(
            compilation=[], generated_inputs=[],
            products=[Product(output='${TREE}/bin/prog', kind='exe',
                              stems=('a',))],
            tests=[], comment='t')
    return _PROTO


def _case_list_project(tmp: Path) -> Path:
    proj = tmp / 'proj'
    (proj / 'decks').mkdir(parents=True)
    (proj / 'decks' / 'b1.DAT').write_text('deck\n')
    (proj / 'cases.txt').write_text(
        'mys;    decks/b1.DAT;   SC/1/DISPLACEMENTS/GID/900/TX;;  0.1;  1e-3%;\n')
    return proj


def test_the_declared_field_model_changes_the_assertion(tmp_path: Path):
    """Without it the plan falls back to a generic comparison.

    This is the measured downgrade: a `field_assertion` naming which COLUMNS
    are physical becomes a `text_lockstep` that compares whole lines. The
    plan still passes — which is the problem.
    """
    from protocolize.onboard.extract import capture_verification

    proj = _case_list_project(tmp_path)
    tree = tmp_path / 'build'
    tree.mkdir()
    field_model = {
        'titles': ['DISPLACEMENTS'],
        'tables': [{'titles': ['DISPLACEMENTS'],
                    'rows': [{'match': {'first_int': True, 'n_tokens': 3},
                              'fields': {'indices': [1, 2]}}]}],
    }

    without = capture_verification(_proto(), tree, tree, proj)
    with_ = capture_verification(
        _proto(), tree, tree, proj,
        facts={'field_model': field_model})

    kinds_without = [a['kind'] for a in without['assertions']]
    kinds_with = [a['kind'] for a in with_['assertions']]
    assert 'field_assertion' in kinds_with, kinds_with
    assert 'field_assertion' not in kinds_without, kinds_without
    assert kinds_without != kinds_with, (
        'the declaration changes the plan, so passing it over cannot be a '
        'no-op')
