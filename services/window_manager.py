import ctypes
from ctypes import wintypes
import logging

logger = logging.getLogger(__name__)


def list_windows() -> list[tuple[int, str]]:
    """Return [(hwnd, title)] for all visible titled windows, sorted by title."""
    results: list[tuple[int, str]] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value.strip()
            if title:
                results.append((hwnd, title))
        return True

    ctypes.windll.user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return sorted(results, key=lambda x: x[1].lower())


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return (x, y, w, h) in screen coordinates, or None if the window is gone."""
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def is_window_valid(hwnd: int) -> bool:
    """Return True if the window still exists and is visible."""
    return bool(
        ctypes.windll.user32.IsWindow(hwnd)
        and ctypes.windll.user32.IsWindowVisible(hwnd)
    )
