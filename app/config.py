from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or ""
MONGO_DATABASE = os.getenv("MONGO_DATABASE") or os.getenv(
    "MONGODB_DATABASE", "social_account_resolver"
)

XHS_BROWSER_PROFILE_DIR = Path(
    os.getenv("XHS_BROWSER_PROFILE_DIR", "./data/xhs-browser-profile")
)
if not XHS_BROWSER_PROFILE_DIR.is_absolute():
    XHS_BROWSER_PROFILE_DIR = PROJECT_ROOT / XHS_BROWSER_PROFILE_DIR

XHS_HEADLESS = _bool_env("XHS_HEADLESS", False)
XHS_MIN_PAGE_INTERVAL_SECONDS = _float_env("XHS_MIN_PAGE_INTERVAL_SECONDS", 5.0)
XHS_NAVIGATION_TIMEOUT_MS = _int_env("XHS_NAVIGATION_TIMEOUT_MS", 30000)
XHS_SEARCH_CACHE_DAYS = _int_env("XHS_SEARCH_CACHE_DAYS", 7)
XHS_PROFILE_CACHE_DAYS = _int_env("XHS_PROFILE_CACHE_DAYS", 14)

