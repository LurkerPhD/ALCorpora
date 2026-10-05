"""The package's own tests.  The repo root goes on ``sys.path`` so
``alcorpora.*`` imports the way it does from the skill's tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
