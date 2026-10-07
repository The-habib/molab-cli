"""
Configuration management for molab-cli.
"""

import json
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path.home() / ".config" / "molab"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

DEFAULT_SESSION_ID = ""
DEFAULT_ORG_ID = ""


def load_config() -> Dict[str, Any]:
    """Load configuration dictionary from disk."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg: Dict[str, Any]) -> None:
    """Save configuration dictionary to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_client_cookie() -> str:
    """Retrieve the persistent Clerk __client cookie."""
    return load_config().get("client_cookie", "")


def set_client_cookie(cookie: str) -> None:
    """Save the persistent Clerk __client cookie."""
    cfg = load_config()
    cfg["client_cookie"] = cookie.strip()
    save_config(cfg)


def get_session_id() -> str:
    """Retrieve the Clerk session ID."""
    return load_config().get("session_id", DEFAULT_SESSION_ID)


def get_org_id() -> str:
    """Retrieve the active organization ID."""
    return load_config().get("org_id", DEFAULT_ORG_ID)
