"""Tk window z-order helpers.

Currently ships one helper, ``install_keep_on_bottom``, used by
``fastrack_hub`` to pin the launcher window beneath every other
top-level window — clicking it does not bring it to the foreground,
yet keyboard shortcuts and widget click handlers keep working
normally.

No-op on non-Windows platforms; the user request is Windows-specific.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Optional

from shared_logging import get_logger

logger = get_logger("shared_window_zorder")


# Win32 SetWindowPos flags / hwnd handles we use below
_HWND_BOTTOM = 1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_KEEP_FLAGS = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE


def install_keep_on_bottom(root: tk.Tk) -> Optional[callable]:
    """Pin ``root`` to the bottom of the Windows z-order.

    Whenever the window receives focus — via click, Alt+Tab, or
    anything else that would normally bring it forward — we drop it
    straight back to the bottom with ``SetWindowPos(HWND_BOTTOM,
    SWP_NOACTIVATE)``. Other apps stay visible on top of FastRack;
    clicks on the parts of FastRack that ARE visible still fire
    button handlers, keyboard shortcuts still work.

    Caveat: there is an unavoidable brief flash where Windows
    activates the window before we can react. The flash is short
    (typically < 50 ms) but is not entirely eliminable from
    user-space without sacrificing keyboard focus
    (``WS_EX_NOACTIVATE`` strips that).

    Returns the bound ``to_back`` callable so the caller can also
    schedule it manually (e.g. after a Toplevel dialog closes).
    Returns None on non-Windows platforms.
    """
    if sys.platform != "win32":
        return None

    import ctypes
    user32 = ctypes.windll.user32

    def to_back(event=None):
        try:
            # winfo_id() gives the Tk widget id; on Windows top-levels,
            # the actual HWND is its parent (Tk wraps the window).
            hwnd = user32.GetParent(root.winfo_id())
            if hwnd:
                user32.SetWindowPos(
                    hwnd, _HWND_BOTTOM, 0, 0, 0, 0, _KEEP_FLAGS,
                )
        except Exception:
            logger.exception("SetWindowPos(HWND_BOTTOM) failed")

    # Initial drop, once Tk has realised the HWND.
    root.after(100, to_back)
    # Re-drop on every focus event.
    root.bind("<FocusIn>", to_back, add="+")
    # Also catch the case where the window is shown / restored
    # from a minimized state.
    root.bind("<Map>", to_back, add="+")
    return to_back


def uninstall_keep_on_bottom(root: tk.Tk) -> None:
    """Remove the keep-on-bottom bindings and restore normal z-order."""
    if sys.platform != "win32":
        return

    try:
        root.unbind("<FocusIn>")
        root.unbind("<Map>")
    except Exception:
        pass

    # Lift back to normal z-order
    try:
        import ctypes
        _HWND_NOTOPMOST = -2
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id())
        if hwnd:
            user32.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, _KEEP_FLAGS)
    except Exception:
        logger.exception("SetWindowPos(HWND_NOTOPMOST) failed")
