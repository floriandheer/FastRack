"""Hand-built scrollable canvases must scroll on a two-finger trackpad swipe, not only on a mouse wheel.

Tk 9 (TIP 684) sends a trackpad scroll as <TouchpadScroll> and keeps <MouseWheel> for real wheels, so a canvas that
binds only <MouseWheel> ignores the trackpad. enable_canvas_scroll gives such a canvas both, like ScrollableFrame.
"""

import sys
import tkinter as tk
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules"))

from shared_scrollable_frame import (  # noqa: E402
    ScrollableFrame, enable_canvas_scroll, scrolls_itself, touchpad_scroll_units,
)

SEQUENCES = ("<MouseWheel>", "<Button-4>", "<Button-5>", "<TouchpadScroll>")


def touchpad_delta(dy):
    """The packed %D value Tk sends: X delta in the high 16 bits, signed Y delta in the low 16."""
    return dy & 0xFFFF


@pytest.fixture
def window(tk_window):
    tk_window.deiconify()  # Tk doesn't deliver generated pointer events to an unmapped window
    tk_window.update()
    yield tk_window
    for seq in SEQUENCES:  # bind_all bindings outlive the widgets that made them
        try:
            tk_window.unbind_all(seq)
        except tk.TclError:
            pass


@pytest.fixture
def supports_touchpad(window):
    try:
        window.bind_all("<TouchpadScroll>", lambda e: None)
    except tk.TclError:
        pytest.skip("this Tk predates TIP 684 and has no <TouchpadScroll>")


def make_canvas(window, content_height=2000, view_height=200):
    canvas = tk.Canvas(window, width=200, height=view_height, highlightthickness=0)
    inner = tk.Frame(canvas, width=200, height=content_height)
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(scrollregion=(0, 0, 200, content_height))
    canvas.pack()
    window.update()
    return canvas, inner


def swipe(widget, dy):
    widget.event_generate("<TouchpadScroll>", delta=touchpad_delta(dy))


def wheel(widget, delta):
    widget.event_generate("<MouseWheel>", delta=delta)


def test_decodes_swipe_direction(window, supports_touchpad):
    canvas, _ = make_canvas(window)
    seen = []
    canvas.bind("<TouchpadScroll>", lambda e: seen.append(touchpad_scroll_units(canvas, e)))
    swipe(canvas, -5)  # fingers moving up: content moves up, view goes down
    swipe(canvas, 5)
    swipe(canvas, 0)
    assert seen == [1, -1, 0]


def test_trackpad_scrolls_the_canvas(window, supports_touchpad):
    canvas, _ = make_canvas(window)
    enable_canvas_scroll(canvas)
    canvas.event_generate("<Enter>")
    assert canvas.yview()[0] == 0.0

    swipe(canvas, -5)
    after_down = canvas.yview()[0]
    assert after_down > 0.0

    swipe(canvas, 5)
    assert canvas.yview()[0] < after_down


def test_mouse_wheel_still_scrolls_the_canvas(window):
    canvas, _ = make_canvas(window)
    enable_canvas_scroll(canvas)
    canvas.event_generate("<Enter>")

    wheel(canvas, -120)
    after_down = canvas.yview()[0]
    assert after_down > 0.0

    wheel(canvas, 120)
    assert canvas.yview()[0] < after_down


def test_small_wheel_deltas_still_scroll(window):
    """Smooth wheels and Tk 8.6 on macOS report deltas far below 120; those used to round down to no movement."""
    canvas, _ = make_canvas(window)
    enable_canvas_scroll(canvas)
    canvas.event_generate("<Enter>")
    wheel(canvas, -3)
    assert canvas.yview()[0] > 0.0


def test_scrolls_when_the_pointer_is_over_content_inside_the_canvas(window, supports_touchpad):
    canvas, inner = make_canvas(window)
    enable_canvas_scroll(canvas)
    canvas.event_generate("<Enter>")
    swipe(inner, -5)
    assert canvas.yview()[0] > 0.0


def test_content_that_fits_does_not_scroll(window, supports_touchpad):
    canvas, _ = make_canvas(window, content_height=100, view_height=200)
    enable_canvas_scroll(canvas)
    canvas.event_generate("<Enter>")
    swipe(canvas, -5)
    wheel(canvas, -120)
    assert canvas.yview()[0] == 0.0


def test_stops_scrolling_once_the_pointer_has_left(window, supports_touchpad):
    canvas, _ = make_canvas(window)
    enable_canvas_scroll(canvas)
    canvas.event_generate("<Enter>")
    canvas.event_generate("<Leave>", rootx=-5000, rooty=-5000)  # far outside any window
    swipe(canvas, -5)
    wheel(canvas, -120)
    assert canvas.yview()[0] == 0.0


def test_only_the_hovered_canvas_scrolls(window, supports_touchpad):
    first, _ = make_canvas(window)
    second, _ = make_canvas(window)
    enable_canvas_scroll(first)
    enable_canvas_scroll(second)
    first.event_generate("<Enter>")
    first.event_generate("<Leave>", rootx=-5000, rooty=-5000)
    second.event_generate("<Enter>")
    swipe(second, -5)
    assert first.yview()[0] == 0.0
    assert second.yview()[0] > 0.0


def test_hover_can_be_a_container_around_the_canvas(window, supports_touchpad):
    container = tk.Frame(window)
    container.pack()
    canvas = tk.Canvas(container, width=200, height=200, highlightthickness=0)
    canvas.pack(side="left")
    tk.Scrollbar(container, command=canvas.yview).pack(side="right", fill="y")
    canvas.create_rectangle(0, 0, 200, 2000)
    canvas.configure(scrollregion=(0, 0, 200, 2000))
    window.update()
    enable_canvas_scroll(canvas, hover=container)
    container.event_generate("<Enter>")
    swipe(canvas, -5)
    assert canvas.yview()[0] > 0.0


def test_destroying_the_canvas_releases_the_global_bindings(window, supports_touchpad):
    canvas, _ = make_canvas(window)
    enable_canvas_scroll(canvas)
    canvas.event_generate("<Enter>")
    assert window.bind_all("<TouchpadScroll>")
    canvas.destroy()
    assert not window.bind_all("<TouchpadScroll>")


def test_a_text_widget_inside_the_canvas_keeps_its_own_scrolling(window):
    canvas, inner = make_canvas(window)
    text = tk.Text(inner, height=3, width=20)
    text.insert("1.0", "\n".join(str(n) for n in range(100)))
    text.pack()
    window.update_idletasks()
    assert scrolls_itself(text)

    short = tk.Text(inner, height=10, width=20)
    short.insert("1.0", "one line")
    short.pack()
    window.update_idletasks()
    assert not scrolls_itself(short)


def test_scrollable_frame_trackpad_behaviour_is_unchanged(window, supports_touchpad):
    frame = ScrollableFrame(window)
    frame.pack()
    tk.Frame(frame.get_frame(), width=200, height=2000).pack()
    frame.canvas.configure(height=200)
    window.update_idletasks()
    frame.event_generate("<Enter>")
    swipe(frame.canvas, -5)
    assert frame.canvas.yview()[0] > 0.0
