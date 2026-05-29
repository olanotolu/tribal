"""Regression tests for _apply_profile_override TRIBAL_HOME guard (issue #22502).

When TRIBAL_HOME is set to the tribal root (e.g. systemd hardcodes
TRIBAL_HOME=/root/.tribal), _apply_profile_override must still read
active_profile and update TRIBAL_HOME to the profile directory.

When TRIBAL_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _run_apply_profile_override(
    tmp_path, monkeypatch, *, tribal_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["TRIBAL_HOME"] after the call,
    or None if unset.
    """
    tribal_root = tmp_path / ".tribal"
    tribal_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (tribal_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (tribal_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if tribal_home is not None:
        monkeypatch.setenv("TRIBAL_HOME", tribal_home)
    else:
        monkeypatch.delenv("TRIBAL_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["tribal", "gateway", "start"])

    from tribal_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("TRIBAL_HOME")


class TestApplyProfileOverrideTribalHomeGuard:
    """Regression guard for issue #22502.

    Verifies that TRIBAL_HOME pointing to the tribal root does NOT suppress
    the active_profile check, while TRIBAL_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_tribal_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """TRIBAL_HOME=/root/.tribal + active_profile=coder must redirect
        TRIBAL_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets TRIBAL_HOME to the tribal root
        and the user switches to a profile via `tribal profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        tribal_root = tmp_path / ".tribal"
        tribal_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            tribal_home=str(tribal_root),
            active_profile="coder",
        )

        assert result is not None, "TRIBAL_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected TRIBAL_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected TRIBAL_HOME to end with 'coder', got: {result!r}"
        )

    def test_tribal_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """TRIBAL_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with TRIBAL_HOME already set to a specific profile must stay in that
        profile.
        """
        tribal_root = tmp_path / ".tribal"
        profile_dir = tribal_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (tribal_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("TRIBAL_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["tribal", "gateway", "start"])

        from tribal_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("TRIBAL_HOME") == str(profile_dir), (
            "TRIBAL_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_tribal_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: TRIBAL_HOME unset + active_profile=coder must set
        TRIBAL_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            tribal_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_tribal_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect TRIBAL_HOME."""
        tribal_root = tmp_path / ".tribal"
        tribal_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("TRIBAL_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["tribal", "gateway", "start"])
        (tribal_root / "active_profile").write_text("default")

        from tribal_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("TRIBAL_HOME") is None
