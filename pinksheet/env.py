"""Tiny helpers for reading settings out of the environment / .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

_FALSE = {"0", "false", "no", "off", ""}


def load_env_file(path: Path) -> None:
    """Load KEY=value lines from .env without overriding real environment variables."""
    if path.exists():
        load_dotenv(path, override=False)


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in _FALSE


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    try:
        return int(value)
    except ValueError:
        return default


def env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return list(default)
    return [part.strip() for part in value.split(",") if part.strip()]
