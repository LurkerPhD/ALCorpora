"""A tree that is not there is an answer, not a crash."""
from pathlib import Path
from alcorpora.onboard.make_extract import make_directories


def test_a_missing_tree_is_not_a_make_directory(tmp_path: Path):
    """Measured: the flow's `tree_subdir` default is `build`; against a
    hand-written make project the capture died with a FileNotFoundError
    traceback before any seam could open. The seam cannot be asked if the
    flow dies first."""
    assert make_directories(tmp_path / 'nope') == []


def test_a_real_make_tree_is_still_found(tmp_path: Path):
    (tmp_path / 'Makefile').write_text('all:\n\t@true\n')
    assert make_directories(tmp_path) == [tmp_path]
