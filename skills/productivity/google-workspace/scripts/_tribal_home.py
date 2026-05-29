"""Resolve TRIBAL_HOME for standalone skill scripts.

Skill scripts may run outside the Tribal process (e.g. system Python,
nix env, CI) where ``tribal_constants`` is not importable.  This module
provides the same ``get_tribal_home()`` and ``display_tribal_home()``
contracts as ``tribal_constants`` without requiring it on ``sys.path``.

When ``tribal_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``tribal_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``TRIBAL_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from tribal_constants import display_tribal_home as display_tribal_home
    from tribal_constants import get_tribal_home as get_tribal_home
except (ModuleNotFoundError, ImportError):

    def get_tribal_home() -> Path:
        """Return the Tribal home directory (default: ~/.tribal).

        Mirrors ``tribal_constants.get_tribal_home()``."""
        val = os.environ.get("TRIBAL_HOME", "").strip()
        return Path(val) if val else Path.home() / ".tribal"

    def display_tribal_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``tribal_constants.display_tribal_home()``."""
        home = get_tribal_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
