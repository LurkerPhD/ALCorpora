"""Running twice and diffing is the deterministic half — and only half.

The mechanism turns "which lines are volatile" from a judgement into a
measurement: run the same binary twice, compare what it printed. No model, no
inference about what a timestamp looks like.

Its LIMIT is the whole reason it is evidence and not an answer, and these
tests pin both the measurement and the limit.
"""

from __future__ import annotations

from pathlib import Path

from protocolize import volatiles


def _run(root: Path, name: str, lines: list[str]) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / 'out.txt').write_text('\n'.join(lines) + '\n')


def test_a_moved_number_is_found(tmp_path: Path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    _run(a, 'c1', [' END TIME: 3:55:16', ' RESULT  1.000000E+00'])
    _run(b, 'c1', [' END TIME: 3:55:11', ' RESULT  1.000000E+00'])
    got = volatiles.between(a, b)
    assert len(got) == 1, got
    assert 'END TIME' in got[0].shape
    assert got[0].cases == 1


def test_a_shape_seen_in_every_case_ranks_first(tmp_path: Path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    for i in range(5):
        _run(a, f'c{i}', [' STAMP 111', f' N {i}'])
        _run(b, f'c{i}', [' STAMP 999', f' N {i}'])
    _run(a, 'rare', [' ONLY 1'])
    _run(b, 'rare', [' ONLY 2'])
    got = volatiles.between(a, b)
    assert got[0].cases == 5, 'a shape in every case is the mechanism'
    assert got[0].shape.startswith('STAMP'), got[0].shape


def test_identical_runs_report_nothing_and_say_so(tmp_path: Path):
    """An honest empty is a finding: the volatility must come from elsewhere."""
    a, b = tmp_path / 'a', tmp_path / 'b'
    _run(a, 'c1', [' SAME 1 2 3'])
    _run(b, 'c1', [' SAME 1 2 3'])
    assert volatiles.between(a, b) == []
    assert 'NO differing lines' in volatiles.report(a, b)


def test_numeric_masking_keeps_the_sentence(tmp_path: Path):
    assert volatiles.shape_of(' B =    21 BBAR = 100') == \
        volatiles.shape_of(' B =     4 BBAR =   3'), (
        'two runs of one pool report must collapse to ONE shape')


def test_it_is_a_LOWER_BOUND_and_the_report_says_so(tmp_path: Path):
    """The measured failure this caveat exists for.

    A corpus's own notes record three related counters moving together. A diff
    of two runs caught two of the three — the third happened to agree — and a
    declaration built from that diff passed 132/132 that day and would have
    failed on another. The report must not read as "this is the set".
    """
    a, b = tmp_path / 'a', tmp_path / 'b'
    _run(a, 'c1', [' B = 21', ' C = 20', ' R = 23'])
    _run(b, 'c1', [' B = 21', ' C = 99', ' R = 88'])   # B agreed by luck
    text = volatiles.report(a, b)
    assert 'lower bound' in text
    assert 'invisible here' in text
    assert 'MECHANISM' in text, (
        'the generalisation the instance teaches must travel with it')
    # And the line that agreed is genuinely absent — the limit is real.
    assert 'B = #' not in text


def test_a_line_whose_text_differs_is_not_hidden(tmp_path: Path):
    """Numbers moving is the common case; text moving must not be masked away."""
    a, b = tmp_path / 'a', tmp_path / 'b'
    _run(a, 'c1', [' STATUS: OK'])
    _run(b, 'c1', [' STATUS: FAILED'])
    got = volatiles.between(a, b)
    assert len(got) == 1
    assert '!=' in got[0].shape, got[0].shape


def test_the_same_day_caveat_is_stated(tmp_path: Path):
    """A date line is INVISIBLE to two runs made on the same day.

    That is the sharpest limit of the whole mechanism: the two runs agree on
    the date, so the date never shows up as volatile, and a rule built from
    the diff will accept a stale one. Measured on a corpus whose reports carry
    `DATE: 10/ 3/26` and a run stamp — both MUST be dropped.
    """
    a, b = tmp_path / 'a', tmp_path / 'b'
    _run(a, 'c1', [' DATE: 10/ 3/26', ' END TIME: 3:55:16'])
    _run(b, 'c1', [' DATE: 10/ 3/26', ' END TIME: 3:55:11'])

    text = volatiles.report(a, b, same_day=True)
    assert 'SAME CALENDAR DAY' in text
    assert 'DATE' not in text.split('CAVEAT')[0], (
        'the date did not move, so it must not be claimed as evidence')
    assert 'read the project\'s own comparison rules' in text

    # A clock-shifted pair would not need the caveat.
    assert 'SAME CALENDAR DAY' not in volatiles.report(a, b, same_day=False)


def test_clock_shift_is_optional_and_reports_itself(tmp_path: Path):
    """`libfaketime` answers "what changes on another day"; its absence is not
    an error, but it must not be silent either."""
    shifted = volatiles.clock_shifted({})
    if shifted:
        assert 'LD_PRELOAD' in shifted
    else:
        assert shifted == {}, (
            'no faketime means an empty env, never a half-applied one')
    assert volatiles.shifted_stamp() == '+1y', (
        'a year, so a line printing only the year moves too')
