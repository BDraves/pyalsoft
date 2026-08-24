"""Update the complete pinned OpenAL Soft release and generated bindings.

The implementation lives in :mod:`tools.openal_update`; this module remains the
stable contributor and automation entry point.
"""

from __future__ import annotations

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.openal_update import main  # noqa: E402

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
