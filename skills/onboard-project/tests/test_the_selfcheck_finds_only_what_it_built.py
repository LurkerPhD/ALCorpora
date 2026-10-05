"""The self-check must not count a file it did not build.

The replay works on a MIRROR of the capture tree, and a build that puts its
products beside its sources leaves those products in the mirror.  So a
product the generated build never ran a command for is still *found* there —
and a comparison that searches the mirror by name then reports it COMPARED,
having compared the project's own file with itself.

That is not a near miss.  It is a self-check that passes while the replay
builds nothing, and it hides an entire backend: a make build's archive and
executables live beside the sources, and every one of them passed this way
(measured).  The fix and this test are the same statement: what the replay
did not build is not there to be found.

Pinned with a make project because that is the shape that exposes it — its
products are IN the source tree, not in a separate build directory — and
with the commands removed rather than the backend broken, so the assertion
is about the self-check and not about any one extractor.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from alcorpora.onboard.selfcheck import self_check
from test_onboard_reads_a_make_build import _build, _make_project

pytestmark = pytest.mark.skipif(
    not (shutil.which('cmake') and shutil.which('ninja')),
    reason='needs cmake and ninja')


def test_a_product_the_replay_never_built_is_not_compared(tmp_path: Path):
    root = tmp_path / 'project'
    _make_project(root)
    _build(root)

    from alcorpora.onboard.extract import extract_protocol
    proto = extract_protocol(root, tree_root=root)
    assert proto.products, 'the fixture must produce something to lose'

    from dataclasses import replace

    # Nothing rebuilds these products now.  Before the fix the mirror still
    # held them, so the self-check found each one and called it COMPARED.
    proto = replace(proto, products=[replace(p, command='')
                                     for p in proto.products])

    report = self_check(proto, tree=root, reference_tree=root,
                        work_root=tmp_path / 'work')

    names = {Path(p.output).name for p in proto.products}
    assert not (names & set(report.compared_names)), (
        'a product the replay never built was reported COMPARED: '
        f'{sorted(names & set(report.compared_names))}')
    assert names <= set(report.missed), (
        f'expected every unbuilt product in `missed`, got {report.missed}')
