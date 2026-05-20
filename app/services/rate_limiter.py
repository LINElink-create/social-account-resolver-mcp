from __future__ import annotations

import threading
import time

from app.config import XHS_MIN_PAGE_INTERVAL_SECONDS

_lock = threading.Lock()
_last_operation_at = 0.0


def wait_for_xhs_slot() -> None:
    global _last_operation_at

    with _lock:
        now = time.monotonic()
        elapsed = now - _last_operation_at
        if elapsed < XHS_MIN_PAGE_INTERVAL_SECONDS:
            time.sleep(XHS_MIN_PAGE_INTERVAL_SECONDS - elapsed)
        _last_operation_at = time.monotonic()

