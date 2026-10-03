"""What a case WRITES is the project's declaration, not a name baked in.

A one-per-dir corpus that writes exactly one artifact needs no declaration; a
corpus whose run also hands state forward names it. The names used to be
hardcoded to one project's (`.out` / `.sof1`), which is a project fact living
in generic code.
"""

from __future__ import annotations

from protocolize.onboard.extract import _declared_produced, _golden_place


def test_a_project_that_needs_no_declaration_gets_one_artifact():
    out = _declared_produced(None)
    assert out == [{'name': '{work}/{input.stem}.out', 'as': 'out'}]


def test_a_declared_suffix_is_used():
    out = _declared_produced({'artifact': {'suffix': '.F06'}})
    assert out[0]['name'] == '{work}/{input.stem}.F06'


def test_state_a_chain_hands_forward_is_declared():
    out = _declared_produced({'artifact': {'suffix': '.out',
                                           'state_suffix': '.sof1'}})
    assert {'name': '{work}/{input.stem}.sof1', 'as': 'state'} in out


def test_the_golden_place_is_the_projects_own_spelling():
    assert _golden_place(None) == ('case_id', '.out')
    assert _golden_place({'artifact': {'suffix': '.out',
                                       'golden_suffix': '.ref'}}) == \
        ('case_id', '.ref')
