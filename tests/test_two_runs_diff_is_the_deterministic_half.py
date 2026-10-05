"""Running twice and diffing is the deterministic half — and only half.

The mechanism turns "which lines are volatile" from a judgement into a
measurement: run the same binary twice, compare what it printed. No model, no
inference about what a timestamp looks like.

Its LIMIT is the whole reason it is evidence and not an answer, and these
tests pin both the measurement and the limit.
"""

from __future__ import annotations

from pathlib import Path

from alcorpora import volatiles


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


def test_the_clock_shift_is_a_timezone_literal():
    """The shift is `TZ`, and it needs nothing installed.

    `TZ` is a standard environment variable, and the two values are POSIX
    LITERALS rather than `Continent/City` names — a name is resolved through
    the system timezone database and fails where that database is absent,
    while a literal is parsed by libc itself. Measured: the two literals put
    two runs of one program on different calendar days, and moved exactly the
    date and run-stamp lines.

    This is also a self-containment property, which is why it is pinned: the
    tool is meant to be run by other people on other machines, so a shift
    that needed a package (`libfaketime`, the earlier version) would be
    unavailable exactly where it is needed.
    """
    shifted = volatiles.clock_shifted({})
    assert shifted['TZ'] == volatiles.SHIFTED_TZ
    assert 'LD_PRELOAD' not in shifted, (
        'the shift must not depend on a preloaded library')
    assert volatiles.SHIFTED_TZ != volatiles.REAL_TZ

    # The literals parse with no database present, and differ by more than a
    # day. Both halves matter: a name would still "not raise" here on a
    # machine that has tzdata, so the assertion is on the OFFSET.
    def offset_hours(tz: str) -> float:
        import subprocess
        env = {'TZ': tz, 'PATH': '/usr/bin:/bin'}
        r = subprocess.run(['date', '+%z'], env=env,
                           capture_output=True, text=True)
        z = r.stdout.strip()
        return int(z[:3]) + int(z[3:]) / 60.0 if z else 0.0

    delta = offset_hours(volatiles.SHIFTED_TZ) - offset_hours(volatiles.REAL_TZ)
    assert abs(delta) >= 24, (
        f'the two literals must be more than a day apart, got {delta}h')


def test_only_undeclared_movements_are_reported(tmp_path: Path):
    """The diff must say what is ALREADY covered, not re-hand the whole set.

    Handing an agent every moving line makes it re-derive rules the plan
    already has — the "answer as a delta" failure in another guise. So the
    plan's own rules are applied first, and what survives is what a rule still
    has to account for. A `mask` counts as coverage too: it is asked by
    RUNNING the declared rules over the pair, not by matching patterns by eye,
    because a mask that only moves part of a line is coverage the rule itself
    decides.
    """
    from alcorpora.volatiles import declared_modes, uncovered, is_covered

    a, b = tmp_path / 'a', tmp_path / 'b'
    _run(a, 'c1', [' DATE: 10/ 3/26', ' RESULT 1.5 ELAPSED 3:55:16',
                   ' UNSTABLE 42'])
    _run(b, 'c1', [' DATE: 10/ 4/26', ' RESULT 1.5 ELAPSED 3:55:19',
                   ' UNSTABLE 99'])

    class _Plan:
        assertions = [{'kind': 'text_lockstep',
                       'drop': [r'^\s*DATE:'],
                       'mask': [[r'ELAPSED \S+', 'ELAPSED <t>']]}]

    modes = declared_modes(_Plan)
    assert modes == (['^\\s*DATE:'], [], [[r'ELAPSED \S+', 'ELAPSED <t>']])

    assert is_covered(' DATE: 10/ 3/26', ' DATE: 10/ 4/26', modes)
    assert is_covered(' RESULT 1.5 ELAPSED 3:55:16',
                      ' RESULT 1.5 ELAPSED 3:55:19', modes), (
        'a mask that fixes the clock leaves the RESULT still compared — so '
        'the pair is covered')
    assert not is_covered(' UNSTABLE 42', ' UNSTABLE 99', modes)

    left = uncovered(a, b, modes)
    shapes = ' '.join(v.shape for v in left)
    assert 'UNSTABLE' in shapes, 'the undeclared movement must be reported'
    assert 'DATE' not in shapes and 'ELAPSED' not in shapes, (
        'declared movements must NOT be handed back as work to do')


def test_a_mask_that_ignores_the_judged_part_does_not_hide_a_real_diff(tmp_path):
    """Coverage must follow the rule, not the pattern's presence.

    A mask on the clock column covers the clock and NOTHING ELSE: if the
    judged number moves too, the pair is NOT covered, however much the line
    otherwise looks handled.
    """
    from alcorpora.volatiles import uncovered

    a, b = tmp_path / 'a', tmp_path / 'b'
    _run(a, 'c1', [' STEP 4 RESULT 1.5 ELAPSED 3:55:16'])
    _run(b, 'c1', [' STEP 4 RESULT 9.9 ELAPSED 3:55:16'])

    class _Plan:
        assertions = [{'mask': [[r'ELAPSED \S+', 'ELAPSED <t>']]}]

    from alcorpora.volatiles import declared_modes
    left = uncovered(a, b, declared_modes(_Plan))
    assert left, ('the RESULT moved and the mask does not touch it, so this '
                  'is NOT covered')


def test_uncovered_agrees_with_the_verdict(tmp_path: Path):
    """`evidence` and `lockstep` must use ONE notion of "covered".

    Measured: asked per RAW line, `uncovered` reported 27 uncovered shapes on
    a corpus whose comparison had ZERO differences — because a rule that DROPS
    a varying line changes the line COUNT, and a per-position question then
    mispairs everything after it. Normalizing FIRST (the same thing `lockstep`
    does) removes the whole class, and this pins that they agree.
    """
    from alcorpora.volatiles import uncovered, declared_modes
    from alcorpora.verify import compare

    a, b = tmp_path / 'a', tmp_path / 'b'
    # Run A has the estimated line twice, run B once — the DROP below must
    # remove it from both, so the surviving sequences have the same length.
    _run(a, 'c1', [' TIME ESTIMATE = 0 SECONDS', ' RESULT 1.5',
                   ' TIME ESTIMATE = 0 SECONDS', ' END'])
    _run(b, 'c1', [' TIME ESTIMATE = 0 SECONDS', ' RESULT 1.5', ' END'])

    class _Plan:
        assertions = [{'kind': 'text_lockstep', 'drop': [r'TIME ESTIMATE'],
                       'rtol': 0.05, 'atol': 0.5}]

    modes = declared_modes(_Plan)
    left = uncovered(a, b, modes, slack=(0.05, 0.5))
    diffs = compare.lockstep(
        (a / 'c1' / 'out.txt').read_text(), (b / 'c1' / 'out.txt').read_text(),
        drop=[r'TIME ESTIMATE'], rtol=0.05, atol=0.5)

    assert not diffs, 'the two runs agree once the varying line is dropped'
    assert not left, (
        'a line the rules DROP cannot leave the sequences mismatched — '
        'evidence must not report it as uncovered')


def test_a_real_difference_is_still_uncovered(tmp_path: Path):
    """The agreement must not be bought by reporting nothing.

    If the judged value itself moves, BOTH `uncovered` and `lockstep` must see
    it — otherwise the fix would have turned "evidence" into a rubber stamp.
    """
    from alcorpora.volatiles import uncovered, declared_modes
    from alcorpora.verify import compare

    a, b = tmp_path / 'a', tmp_path / 'b'
    _run(a, 'c1', [' TIME ESTIMATE = 0 SECONDS', ' RESULT 1.5'])
    _run(b, 'c1', [' TIME ESTIMATE = 0 SECONDS', ' RESULT 9.9'])

    class _Plan:
        assertions = [{'kind': 'text_lockstep', 'drop': [r'TIME ESTIMATE'],
                       'rtol': 0.05, 'atol': 0.5}]

    modes = declared_modes(_Plan)
    left = uncovered(a, b, modes, slack=(0.05, 0.5))
    diffs = compare.lockstep(
        (a / 'c1' / 'out.txt').read_text(), (b / 'c1' / 'out.txt').read_text(),
        drop=[r'TIME ESTIMATE'], rtol=0.05, atol=0.5)

    assert diffs, 'a moved judged value IS a difference'
    assert left and 'RESULT' in ' '.join(v.shape for v in left), (
        'and it must be reported as uncovered')
