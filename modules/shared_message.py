"""
Modal message box for headless PipelineScripts.

Scripts launched from the hub that have no window of their own (they just
start another program) still need a way to tell the user something went
wrong - logging alone would be invisible. This shows a plain tk messagebox
on a hidden root.
"""

from shared_logging import get_logger

logger = get_logger(__name__)


def show_message(title: str, message: str, error: bool = True):
    """Show `message` in a modal error (default) or info box. Never raises."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        show = messagebox.showerror if error else messagebox.showinfo
        show(title, message, parent=root)
        root.destroy()
    except Exception as e:
        logger.warning(f"Could not show message box: {e}")
