"""Tests for get_tribal_home() profile-mode fallback warning.

Regression test for https://github.com/Tribal/tribal/issues/18594.

When TRIBAL_HOME is unset but an active_profile file indicates a non-default
profile is active, get_tribal_home() should:
  1. STILL return ~/.tribal (raising would brick 30+ module-level callers)
  2. Emit a loud one-shot warning to stderr so operators can diagnose
     cross-profile data contamination after the fact.

The warning goes to stderr directly (not through logging) because this
function is called at module-import time from 30+ sites, often before the
logging subsystem has been configured.
"""

from pathlib import Path

import pytest


@pytest.fixture
def fresh_constants(monkeypatch, tmp_path):
    """Import tribal_constants fresh and reset the one-shot warn flag."""
    import importlib
    import tribal_constants
    importlib.reload(tribal_constants)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("TRIBAL_HOME", raising=False)
    return tribal_constants


class TestGetTribalHomeProfileWarning:
    def test_classic_mode_no_active_profile_no_warning(
        self, fresh_constants, tmp_path, capsys
    ):
        """Classic mode: no active_profile file → silent, returns ~/.tribal."""
        result = fresh_constants.get_tribal_home()
        assert result == tmp_path / ".tribal"
        assert "TRIBAL_HOME fallback" not in capsys.readouterr().err

    def test_default_active_profile_no_warning(
        self, fresh_constants, tmp_path, capsys
    ):
        """active_profile=default → still no warning, returns ~/.tribal."""
        tribal_dir = tmp_path / ".tribal"
        tribal_dir.mkdir()
        (tribal_dir / "active_profile").write_text("default\n")
        result = fresh_constants.get_tribal_home()
        assert result == tmp_path / ".tribal"
        assert "TRIBAL_HOME fallback" not in capsys.readouterr().err

    def test_named_profile_unset_home_warns_once(
        self, fresh_constants, tmp_path, capsys
    ):
        """active_profile=coder + TRIBAL_HOME unset → warn loudly, still return fallback."""
        tribal_dir = tmp_path / ".tribal"
        tribal_dir.mkdir()
        (tribal_dir / "active_profile").write_text("coder\n")

        result = fresh_constants.get_tribal_home()

        # 1. Still returns the fallback — no import-time crash
        assert result == tmp_path / ".tribal"
        # 2. Stderr got the warning exactly once
        err = capsys.readouterr().err
        assert err.count("TRIBAL_HOME fallback") == 1
        assert "'coder'" in err
        assert "#18594" in err

        # 3. One-shot: second and third calls don't re-warn
        fresh_constants.get_tribal_home()
        fresh_constants.get_tribal_home()
        err2 = capsys.readouterr().err
        assert "TRIBAL_HOME fallback" not in err2

    def test_tribal_home_set_suppresses_warning(
        self, fresh_constants, tmp_path, capsys, monkeypatch
    ):
        """Even if active_profile is 'coder', setting TRIBAL_HOME suppresses warning."""
        profile_dir = tmp_path / ".tribal" / "profiles" / "coder"
        profile_dir.mkdir(parents=True)
        (tmp_path / ".tribal" / "active_profile").write_text("coder\n")
        monkeypatch.setenv("TRIBAL_HOME", str(profile_dir))

        result = fresh_constants.get_tribal_home()

        assert result == profile_dir
        assert "TRIBAL_HOME fallback" not in capsys.readouterr().err

    def test_unreadable_active_profile_no_crash(
        self, fresh_constants, tmp_path, capsys
    ):
        """active_profile that can't be decoded → fall through silently."""
        tribal_dir = tmp_path / ".tribal"
        tribal_dir.mkdir()
        # Write bytes that aren't valid utf-8
        (tribal_dir / "active_profile").write_bytes(b"\xff\xfe\x00\x00")

        result = fresh_constants.get_tribal_home()

        assert result == tmp_path / ".tribal"
        # Shouldn't crash; shouldn't warn either (can't tell what profile was intended)
        assert "TRIBAL_HOME fallback" not in capsys.readouterr().err

    def test_empty_active_profile_no_warning(
        self, fresh_constants, tmp_path, capsys
    ):
        """Empty active_profile file → treated as default, no warning."""
        tribal_dir = tmp_path / ".tribal"
        tribal_dir.mkdir()
        (tribal_dir / "active_profile").write_text("")

        result = fresh_constants.get_tribal_home()

        assert result == tmp_path / ".tribal"
        assert "TRIBAL_HOME fallback" not in capsys.readouterr().err
