"""A seam's answer is a typed contract, and the flow refuses what it breaks.

The point of moving judgement behind a schema is that the flow can say NO. An
answer that is not JSON, that never says which kind it is, that names a kind
the flow does not implement, or that fills its kind out incompletely, must be
refused — never adopted on the assumption that it was probably meant well.

`check` is the load-bearing one. A patch without a `check` cannot be told
apart from an already-applied patch, so it is neither idempotent nor
reproducible: it would run again on every admission, and a reader could not
confirm what state the tree is in.
"""

from __future__ import annotations

import json

import pytest

from protocolize import seams


def _answer(**payload) -> str:
    return json.dumps(payload)


def test_a_bare_string_kind_is_read():
    resp = seams.parse(_answer(kind='facts', value={'a': 1}))
    assert resp.kind == 'facts'
    assert resp.ok


def test_the_agents_print_envelope_is_unwrapped():
    """The envelope is the CALLER's tool's shape, not part of the contract."""
    inner = _answer(kind='declaration', value={'comparison': {}})
    resp = seams.parse(json.dumps({'result': inner, 'is_error': False}))
    assert resp.kind == 'declaration'


def test_an_error_envelope_is_refused():
    wrapped = json.dumps({'result': 'boom', 'is_error': True})
    with pytest.raises(seams.SeamError, match='reported an error'):
        seams.parse(wrapped)


def test_text_that_is_not_json_is_refused():
    with pytest.raises(seams.SeamError, match='not JSON'):
        seams.parse('I think the project probably uses CMake')


def test_an_answer_without_a_kind_is_refused():
    with pytest.raises(seams.SeamError, match='no `kind`'):
        seams.parse(_answer(configure='make'))


def test_an_unknown_kind_is_refused():
    with pytest.raises(seams.SeamError, match='not one of'):
        seams.parse(_answer(kind='native_commandz'))


def test_a_json_array_is_refused():
    """A bare list has no kind, and guessing one invents the contract."""
    with pytest.raises(seams.SeamError, match='not an object'):
        seams.parse('[{"kind": "facts"}]')


def test_native_commands_accepts_one_line_or_many():
    one = seams.parse(_answer(kind='native_commands', build='make -C src'))
    assert seams.problems(one) == []
    many = seams.parse(_answer(kind='native_commands',
                               configure=[], build=['make -C a', 'make -C b'],
                               test=['./check.sh']))
    assert seams.problems(many) == []


def test_native_commands_without_a_build_is_refused():
    resp = seams.parse(_answer(kind='native_commands', configure='cmake -S .'))
    assert any('`build` is empty' in m for m in seams.problems(resp))


def test_a_non_string_command_is_refused():
    resp = seams.parse(_answer(kind='native_commands', build=['ok', 7]))
    assert any('`build`' in m for m in seams.problems(resp))


def test_a_patch_without_a_check_is_refused():
    """The failure this exists to prevent: an op that cannot be confirmed."""
    resp = seams.parse(_answer(kind='prepare_ops', ops=[
        {'op': 'fix-paths', 'run': 'sed -i s|\\\\|/| Makefile'}]))
    assert any('no `check`' in m for m in seams.problems(resp))


def test_a_patch_with_a_check_is_accepted():
    resp = seams.parse(_answer(kind='prepare_ops', ops=[
        {'op': 'fix-paths',
         'check': '! grep -q "\\\\" Makefile',
         'run': 'sed -i s|\\\\|/| Makefile'}]))
    assert seams.problems(resp) == []


def test_an_empty_patch_list_is_refused():
    resp = seams.parse(_answer(kind='prepare_ops', ops=[]))
    assert any('non-empty' in m for m in seams.problems(resp))


def test_facts_and_declaration_require_their_value_object():
    for kind in ('facts', 'declaration'):
        bad = seams.parse(_answer(kind=kind, value='see README'))
        assert any('`value`' in m for m in seams.problems(bad))


def test_a_facts_object_the_capture_cannot_read_is_refused():
    """The measurement this exists for.

    A `facts` answer using top-level `env` / `home` / `config` / `resources`
    instead of `preparation.home_env` / `preparation.resource_dir` passed
    every check the flow had, changed nothing, and the run then reported
    "reference run captured no goldens" — a failure about the PROJECT for a
    fact about the ANSWER. An inert declaration is not a declaration.
    """
    inert = seams.parse(_answer(kind='facts', value={
        'env': {'NASTRANHOME': '/x'},
        'home': '/x',
        'config': 'bin/nastran.nml',
        'resources': {'rigid_format_dir': 'dmap/rf'},
        'scratch_dir': 'scratch',
    }))
    problems = seams.problems(inert)
    assert problems, 'a declaration the capture cannot read was accepted'
    assert 'no key the capture reads' in problems[0], problems


def test_a_facts_object_the_capture_reads_is_accepted():
    ok = seams.parse(_answer(kind='facts', value={
        'preparation': {'home_env': 'NASTRANHOME',
                        'config_env': 'NASTRANCFG',
                        'config_glob': 'nastran.nml',
                        'resource_dir': 'dmap',
                        'scratch_subdir': '../scratch',
                        'substitutions': [['dmap\\rf', 'dmap/rf']],
                        'alias_upper': True}}))
    assert seams.problems(ok) == []


def test_an_unknown_preparation_key_is_refused():
    """`preparation` has a fixed vocabulary; a near-miss is inert too."""
    near = seams.parse(_answer(kind='facts', value={
        'preparation': {'home_dir': 'NASTRANHOME'}}))
    problems = seams.problems(near)
    assert any('unknown key' in m for m in problems), problems


def test_a_declaration_without_a_comparison_is_refused():
    """The capture reads `comparison.drop/rtol/atol` and nothing else."""
    empty = seams.parse(_answer(kind='declaration', value={'rtol': 0.05}))
    problems = seams.problems(empty)
    assert any('no `comparison`' in m for m in problems), problems
    good = seams.parse(_answer(kind='declaration', value={
        'comparison': {'drop': ['^TIME'], 'rtol': 0.05, 'atol': 0.5}}))
    assert seams.problems(good) == []


def test_a_protocol_gap_must_name_what_is_missing():
    ok = seams.parse(_answer(kind='protocol_gap',
                             missing='how the project is built natively',
                             evidence='no build system in the tree'))
    assert seams.problems(ok) == []
    bad = seams.parse(_answer(kind='protocol_gap', evidence='something'))
    assert any('`missing`' in m for m in seams.problems(bad))


def test_an_object_fenced_in_prose_is_read():
    """A model wraps its answer in explanation and a code fence.

    Measured: a real seam answer was "I have enough evidence. …" followed by a
    ```json block holding a complete and CORRECT `native_commands` ending
    `"tree_subdir": "source"` — and the run threw it away, because `result` was
    required to be nothing but JSON. Three earlier runs had happened to emit
    bare JSON, so the requirement looked safe.

    Reading the object out of prose is not laxity: `kind` and the schema are
    still enforced on whatever comes back, so this decides only WHICH text is
    read.
    """
    fenced = (
        'I have enough evidence. The tree has no CMakeLists.txt.\n\n'
        '```json\n'
        '{"kind": "native_commands", "build": ["make -C a"], '
        '"tree_subdir": "source"}\n'
        '```')
    resp = seams.parse(fenced)
    assert resp.kind == 'native_commands'
    assert resp.payload['tree_subdir'] == 'source'
    assert seams.problems(resp) == []


def test_a_fenced_object_inside_the_envelope_is_read_too():
    inner = ('Here you go:\n```json\n'
             '{"kind": "facts", "value": {"preparation": {}}}\n```')
    resp = seams.parse(json.dumps({'result': inner, 'is_error': False}))
    assert resp.kind == 'facts'


def test_prose_still_does_not_excuse_a_missing_kind():
    """The extraction is not a relaxation of the contract."""
    with pytest.raises(seams.SeamError, match='no `kind`'):
        seams.parse('```json\n{"value": 1}\n```')


def test_text_with_no_object_at_all_is_still_refused():
    with pytest.raises(seams.SeamError, match='not JSON'):
        seams.parse('the project uses GNU make and has four executables')
