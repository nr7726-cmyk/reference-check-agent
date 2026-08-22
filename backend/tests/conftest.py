from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def synthetic_fixture(name: str) -> bytes:
    return (REPOSITORY_ROOT / "fixtures" / "synthetic" / name).read_bytes()
