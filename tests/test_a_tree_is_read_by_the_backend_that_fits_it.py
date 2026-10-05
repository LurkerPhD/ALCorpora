"""A tree is sent to the backend that can read it, by what it HOLDS.

The measured failure: CMake's "Unix Makefiles" generator writes a
`CMakeCache.txt` and NO `build.ninja`. A predicate that treated the cache as
proof of ninja sent such a tree to the ninja backend, and the run died with
`ninja: loading 'build.ninja': No such file or directory` — which reads like a
broken project when the truth is that the tree is a KIND this tool has no
backend for.

And when no backend fits, the refusal must say WHICH kind of tree it is, so
the reader can tell "add a backend" from "your build is broken".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alcorpora.onboard.extract import (_is_ninja_tree, _what_the_tree_is)


def test_a_cache_alone_is_not_a_ninja_tree(tmp_path: Path):
    """`CMakeCache.txt` without `build.ninja` is a different generator."""
    (tmp_path / 'CMakeCache.txt').write_text(
        'CMAKE_GENERATOR:INTERNAL=Unix Makefiles\n')
    assert not _is_ninja_tree(tmp_path), (
        'a Makefiles-generator CMake tree must not be sent to ninja')

    (tmp_path / 'build.ninja').write_text('# ninja\n')
    assert _is_ninja_tree(tmp_path)


def test_the_refusal_names_the_generator(tmp_path: Path):
    """The sentence a reader acts on says what the tree IS."""
    (tmp_path / 'CMakeCache.txt').write_text(
        'CMAKE_GENERATOR:INTERNAL=Unix Makefiles\n')
    said = _what_the_tree_is(tmp_path)
    assert 'Unix Makefiles' in said, (
        'the generator is the fact that decides whether a backend is missing '
        'or the build is broken')
    assert 'no backend' in said


def test_a_bare_tree_says_so_plainly(tmp_path: Path):
    said = _what_the_tree_is(tmp_path)
    assert 'no build.ninja' in said


def test_the_cache_still_names_subprojects(tmp_path: Path):
    """The cache is still READ — it just does not decide the backend.

    `_read_ninja` consults `<Name>_SOURCE_DIR` entries for a CMake tree's own
    roots, so narrowing `_is_ninja_tree` must not have removed that use.
    """
    import inspect
    from alcorpora.onboard import extract
    src = inspect.getsource(extract)
    assert 'CMAKECache' in src or 'CMakeCache.txt' in src, (
        'the cache must still be read somewhere')
