"""The fetch skill's script must not call a helper it no longer defines.

Measured: a refactor dropped `_language` while `prove_legal` still called it
on the UNCONDITIONAL path between §A passing and §B running, so EVERY project
that passed §A crashed with a NameError — §B could never run. A fresh-context
agent hit it while admitting a real project.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

_SCRIPT = (Path(__file__).resolve().parents[1]
           / 'skills/fetch-benchmark-project/scripts/fetch.py')


def test_every_called_name_is_defined_or_builtin():
    tree = ast.parse(_SCRIPT.read_text(encoding='utf-8'))
    defined = set(dir(builtins)) | {'__name__', '__file__'}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                defined.add((a.asname or a.name).split('.')[0])
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    missing = sorted(called - defined)
    assert not missing, f'fetch.py calls undefined name(s): {missing}'
