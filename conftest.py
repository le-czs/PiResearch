"""Let the project-root pytest command see FrontierAgent packages."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _path in (_ROOT, _ROOT / "FrontierAgent"):
    text = str(_path)
    if text not in sys.path:
        sys.path.insert(0, text)
