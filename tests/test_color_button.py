"""Coloured buttons that must also show their colours on macOS.

Classic tk.Button is a native Aqua control on a Mac and ignores bg/fg, so every coloured button has to be built
with make_button()/make_toggle_button() (ColorButton there, the stock widget everywhere else)."""

import ast
import sys
import tkinter as tk
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_color_button as scb  # noqa: E402
from shared_color_button import ColorButton, ColorRadioButton, make_button, make_toggle_button  # noqa: E402

COLOUR_OPTIONS = {
    "bg", "fg", "background", "foreground", "activebackground", "activeforeground", "disabledforeground",
    "highlightbackground", "highlightcolor", "selectcolor",
}
SKIP_DIRS = {"tests", "__pycache__", "venv", ".venv", "node_modules", "Fastrack.app"}


def _dotted(node):
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def _is_falsy_constant(node):
    return isinstance(node, ast.Constant) and not node.value


def _raw_coloured_buttons(repo=REPO_ROOT):
    """file:line of every tk.Button with colour options (or a **splat that may hide them) and of every
    tk.Radiobutton drawn as a button (indicatoron=False) - the widgets Aqua paints as plain native buttons."""
    found = []
    for path in sorted(repo.rglob("*.py")):
        rel = path.relative_to(repo)
        if SKIP_DIRS & set(rel.parts) or rel == Path("modules/shared_color_button.py"):
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)    # unrelated escape-sequence nags in other files
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            if name in ("tk.Button", "tkinter.Button"):
                options = {kw.arg for kw in node.keywords}
                if options & COLOUR_OPTIONS or None in options:
                    found.append(f"{rel}:{node.lineno} {name}")
            elif name in ("tk.Radiobutton", "tkinter.Radiobutton"):
                if any(kw.arg == "indicatoron" and _is_falsy_constant(kw.value) for kw in node.keywords):
                    found.append(f"{rel}:{node.lineno} {name}(indicatoron=False)")
    return found


def test_no_raw_coloured_tk_button_in_the_code():
    offenders = _raw_coloured_buttons()
    assert not offenders, (
        "These buttons render as plain white boxes on macOS; build them with "
        "shared_color_button.make_button / make_toggle_button instead:\n  " + "\n  ".join(offenders))


def test_the_scan_flags_raw_coloured_buttons_and_nothing_else(tmp_path):
    (tmp_path / "bad.py").write_text(
        "import tkinter as tk\n"
        "tk.Button(p, text='a', bg='red')\n"                    # line 2: colour
        "tk.Button(p, text='b', **style)\n"                     # line 3: a splat may hide colours
        "tk.Radiobutton(p, indicatoron=False, selectcolor='x')\n"   # line 4: drawn as a button
        "tk.Button(p, text='plain', command=f, width=3)\n"      # fine: native look wanted
        "tk.Radiobutton(p, text='r', bg='red')\n"               # fine: native radio dot, bg honoured
        "tk.Checkbutton(p, text='c', bg='red', selectcolor='x')\n"
        "make_button(p, text='ok', bg='red')\n"
    )
    assert [f.split(" ")[0] for f in _raw_coloured_buttons(tmp_path)] == ["bad.py:2", "bad.py:3", "bad.py:4"]


# ---------------------------------------------------------------- ColorButton behaviour

@pytest.fixture
def root(tk_window):
    return tk_window


def test_command_runs_on_invoke_but_not_while_disabled(root):
    calls = []
    btn = ColorButton(root, text="Go", command=lambda: calls.append(1))
    btn.invoke()
    btn.config(state=tk.DISABLED)
    btn.invoke()
    btn["state"] = tk.NORMAL
    btn.invoke()
    assert calls == [1, 1]
    assert btn.cget("state") == tk.NORMAL and btn["state"] == tk.NORMAL


def test_command_can_be_replaced_with_config(root):
    calls = []
    btn = ColorButton(root, text="Go")
    btn.config(command=lambda: calls.append("new"))
    btn.invoke()
    assert calls == ["new"] and callable(btn.cget("command"))


def test_tk_button_style_kwargs_are_accepted(root):
    btn = ColorButton(root, text="Flat", bg="#27ae60", fg="white", relief=tk.FLAT, borderwidth=0, bd=0, cursor="hand2",
                      padx=15, pady=6, anchor="w", font=("Arial", 9), activebackground="#16a34a",
                      activeforeground="white", takefocus=True)
    assert btn.cget("relief") == tk.FLAT and int(btn.cget("bd")) == 0
    assert btn.cget("bg") == "#27ae60" and btn.cget("text") == "Flat"


def test_borderwidth_alias_beats_the_default_border(root):
    assert int(ColorButton(root, text="x", borderwidth=0).cget("bd")) == 0
    assert int(ColorButton(root, text="x").cget("bd")) == 2


def test_press_feedback_restores_the_configured_relief(root):
    btn = ColorButton(root, text="x", relief=tk.FLAT)
    btn.flash()
    assert btn.cget("relief") == tk.SUNKEN
    root.after(200, root.quit)
    root.mainloop()
    assert btn.cget("relief") == tk.FLAT


def test_command_that_destroys_the_button_leaves_no_error(root):
    errors = []
    root.report_callback_exception = lambda *exc: errors.append(exc)
    holder = {}
    btn = ColorButton(root, text="Close", command=lambda: holder["btn"].destroy())
    holder["btn"] = btn
    btn.pack()
    root.update()
    btn._on_press(None)
    root.after(250, root.quit)
    root.mainloop()
    assert not errors


def test_hover_colours_follow_activebackground_and_config(root):
    btn = ColorButton(root, text="x", bg="#111111", fg="#eeeeee", activebackground="#222222", activeforeground="#ffffff")
    btn.pack()
    root.update()
    btn._on_enter(None)
    assert str(tk.Label.cget(btn, "bg")) == "#222222" and str(tk.Label.cget(btn, "fg")) == "#ffffff"
    btn.config(bg="#333333")                       # recoloured while hovered: still shows the hover colour
    assert str(tk.Label.cget(btn, "bg")) == "#222222"
    btn._on_leave(None)
    assert str(tk.Label.cget(btn, "bg")) == "#333333" and str(tk.Label.cget(btn, "fg")) == "#eeeeee"


def test_disabled_look_keeps_the_background(root):
    btn = ColorButton(root, text="x", bg="#27ae60", fg="white", state=tk.DISABLED)
    assert str(tk.Label.cget(btn, "bg")) == "#27ae60"
    assert str(tk.Label.cget(btn, "fg")) == scb.DISABLED_FG
    btn.config(disabledforeground="#abcdef")
    assert str(tk.Label.cget(btn, "fg")) == "#abcdef"


def test_highlight_ring_is_drawn_by_frames_not_by_the_label(root):
    # Aqua Tk draws a Label's own highlight ring at the wrong size (stray dark lines inside the button).
    btn = ColorButton(root, text="x", highlightthickness=2, highlightbackground="#000001", highlightcolor="#ff0000")
    assert int(tk.Label.cget(btn, "highlightthickness")) == 0
    assert int(btn.cget("highlightthickness")) == 2
    ring = [w for w in btn.winfo_children() if isinstance(w, tk.Frame)]
    assert len(ring) == 4 and {str(w.cget("bg")) for w in ring} == {"#000001"}
    btn.config(highlightbackground="#000002")
    assert {str(w.cget("bg")) for w in ring} == {"#000002"}


# ---------------------------------------------------------------- ColorRadioButton behaviour

def test_toggle_button_repaints_with_selectcolor_and_sets_its_variable(root):
    var = tk.StringVar(root, "list")
    calls = []
    grid = ColorRadioButton(root, text="grid", variable=var, value="grid", bg="#1c2128", fg="#8b949e",
                            selectcolor="#58a6ff", activebackground="#2d333b", indicatoron=False,
                            command=lambda: calls.append(var.get()))
    lst = ColorRadioButton(root, text="list", variable=var, value="list", bg="#1c2128", selectcolor="#58a6ff",
                           indicatoron=False)
    assert str(tk.Label.cget(lst, "bg")) == "#58a6ff" and str(tk.Label.cget(grid, "bg")) == "#1c2128"
    grid.invoke()
    assert var.get() == "grid" and calls == ["grid"]
    assert str(tk.Label.cget(grid, "bg")) == "#58a6ff" and str(tk.Label.cget(lst, "bg")) == "#1c2128"
    grid.config(fg="white")                          # the callers recolour the selected glyph this way
    assert str(tk.Label.cget(grid, "fg")) == "white"


def test_toggle_button_removes_its_variable_trace_on_destroy(root):
    var = tk.StringVar(root, "a")
    btn = ColorRadioButton(root, text="a", variable=var, value="a", selectcolor="#58a6ff")
    btn.destroy()
    assert var.trace_info() == []
    var.set("b")                                     # must not touch the destroyed widget


# ---------------------------------------------------------------- the platform switch

def test_make_button_is_the_stock_button_off_macos(root, monkeypatch):
    monkeypatch.setattr(scb, "USE_COLOR_BUTTON", False)
    btn = make_button(root, text="x", bg="#27ae60", fg="white", relief=tk.FLAT, padx=4)
    assert type(btn) is tk.Button and btn.cget("bg") == "#27ae60"


def test_make_button_is_a_colorbutton_on_macos(root, monkeypatch):
    monkeypatch.setattr(scb, "USE_COLOR_BUTTON", True)
    assert isinstance(make_button(root, text="x", bg="#27ae60", fg="white", relief=tk.FLAT), ColorButton)


@pytest.mark.parametrize("use_color_button, expected", [(False, tk.Radiobutton), (True, ColorRadioButton)])
def test_make_toggle_button_accepts_the_radiobutton_kwargs(root, monkeypatch, use_color_button, expected):
    monkeypatch.setattr(scb, "USE_COLOR_BUTTON", use_color_button)
    var = tk.StringVar(root, "a")
    btn = make_toggle_button(root, text="a", variable=var, value="a", bg="#1c2128", fg="#8b949e",
                             selectcolor="#58a6ff", activebackground="#2d333b", activeforeground="white",
                             indicatoron=False, padx=10, pady=3, font=("Arial", 12, "bold"), borderwidth=0,
                             highlightthickness=0, command=lambda: None)
    assert type(btn) is expected
    if expected is tk.Radiobutton:
        assert not int(btn.cget("indicatoron"))
