"""A case-list corpus's REPORT name is observed, not assumed.

The capture named a case's report after the DECK's own suffix — a `foo.bdf`
deck was expected to write `foo.out`.  That holds only when a program writes
its report beside its input under the input's extension.  A corpus whose
decks are `.bdf` and whose program writes `.F06` was then judged against an
artifact that is never created, so EVERY case failed its artifact check, the
reference run captured nothing, and the project was reported unverifiable
while verifying perfectly well.

Such a corpus also keeps its stored reference output in the tree, named by
the deck's stem with the suffix the program writes.  That is the project
stating the answer on disk, so the capture reads it there instead of
assuming.
"""

from __future__ import annotations

from pathlib import Path

from alcorpora.onboard.extract import (_artifact_spelling,
                                         _golden_spelling,
                                         _observed_output_suffix)


def _corpus(tmp_path: Path, deck_names, ref_names, decks_in_subdir=True):
    """A case-list corpus: decks, and stored references named by deck stem."""
    root = tmp_path / 'validation'
    decks_dir = root / ('decks' if decks_in_subdir else '.')
    refs = root / 'reference_output'
    decks_dir.mkdir(parents=True)
    refs.mkdir(parents=True)
    for name in deck_names:
        (decks_dir / name).write_text('x')
    for name in ref_names:
        (refs / name).write_text('y')
    return root, [str(Path(n).with_suffix('')) + Path(n).suffix
                  for n in deck_names], decks_dir


def test_the_report_suffix_is_read_from_the_stored_references(tmp_path):
    root, decks, decks_dir = _corpus(
        tmp_path,
        ['a.bdf', 'b.bdf', 'c.bdf', 'd.bdf'],
        ['a.F06', 'b.F06', 'c.F06'])
    assert _observed_output_suffix(root / 'cases.txt', decks, decks_dir) \
        == '.F06'


def test_the_deck_directory_itself_does_not_name_the_report(tmp_path):
    """The decks carry the INPUT's suffix; using it would be the old bug."""
    root, decks, decks_dir = _corpus(
        tmp_path,
        ['a.bdf', 'b.bdf', 'c.bdf', 'd.bdf'],
        ['a.F06', 'b.F06', 'c.F06'])
    # `.bdf` appears four times (the decks) and `.F06` three times; the deck
    # directory must be skipped or the deck suffix would win on count.
    assert _observed_output_suffix(root / 'cases.txt', decks, decks_dir) \
        != '.bdf'


def test_one_stray_file_does_not_name_the_report(tmp_path):
    """A report suffix must explain a real share of the decks, or it is some
    unrelated file that happens to match a stem."""
    root, decks, decks_dir = _corpus(
        tmp_path,
        [f'c{i}.bdf' for i in range(20)],
        ['c0.F06'])
    assert _observed_output_suffix(root / 'cases.txt', decks, decks_dir) == ''


def test_nothing_observed_leaves_the_capture_exactly_as_it_was(tmp_path):
    """No evidence, no change: the plan keeps `{deck_out}` and the deck's own
    suffix is what the fallback has always meant."""
    root, decks, decks_dir = _corpus(
        tmp_path, ['a.bdf', 'b.bdf'], [])
    observed = _observed_output_suffix(root / 'cases.txt', decks, decks_dir)
    assert observed == ''
    assert _artifact_spelling(None, observed) == '{deck_out}'
    assert _golden_spelling(None, observed) == '{deck_out}'


def test_a_declaration_still_wins_over_observation():
    """The project's own facts are the strongest statement; observation is
    the fallback for a project that never wrote one down."""
    assert _artifact_spelling({'artifact': {'suffix': '.out'}}, '.F06') == \
        '{work}/{input.stem}.out'
    assert _artifact_spelling(None, '.F06') == '{work}/{input.stem}.F06'
    assert _golden_spelling({'artifact': {'golden_suffix': '.ref'}}, '.F06') \
        == '{deck_stem}.ref'
    assert _golden_spelling(None, '.F06') == '{deck_stem}.F06'
