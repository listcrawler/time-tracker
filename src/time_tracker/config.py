from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


def detect_system_dark() -> bool | None:
    """Probe the OS for its current light/dark preference. Returns None if unknown."""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            return r.returncode == 0 and "dark" in r.stdout.lower()
        if sys.platform == "linux":
            r = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if r.returncode == 0:
                return "dark" in r.stdout.lower()
            r = subprocess.run(
                ["kreadconfig5", "--group", "General", "--key", "ColorScheme"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if r.returncode == 0:
                return "dark" in r.stdout.lower()
        if sys.platform == "win32":
            import winreg  # type: ignore[import]

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return val == 0
    except Exception:
        pass
    return None


_CONFIG_DIR = Path.home() / ".config" / "time-tracker"
_DATA_DIR = Path.home() / ".local" / "share" / "time-tracker"

CONFIG_PATH = _CONFIG_DIR / "config.json"


class GitHubAccount(BaseModel):
    host: str = "github.com"
    token: str | None = None  # if None, resolved via `gh auth token [--hostname HOST]`
    owner: str = ""  # default owner / org
    repo: str = ""  # default repo name (without owner)


class Config(BaseModel):
    backend: Literal["json", "sqlite", "postgres"] = "json"
    json_path: Path = _DATA_DIR / "entries.json"
    sqlite_path: Path = _DATA_DIR / "entries.db"
    postgres_url: str = "postgresql://time-tracker@localhost:5432/time-tracker"
    # Legacy single-account fields (kept for backward compatibility)
    github_token: str | None = None
    github_host: str = "github.com"
    github_repo: str | None = None  # e.g. "owner/repo"
    # Multi-account — takes precedence over the legacy fields above when non-empty
    github_accounts: list[GitHubAccount] = []
    color_scheme: Literal["dark", "light", "auto"] = "auto"
    dark_theme: str = "textual-dark"
    light_theme: str = "textual-light"

    @classmethod
    def load(cls) -> Config:
        if CONFIG_PATH.exists():
            return cls.model_validate_json(CONFIG_PATH.read_text())
        return cls()

    def save(self) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(self.model_dump_json(indent=2))
