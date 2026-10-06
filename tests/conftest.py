"""Shared test fixtures."""

import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def _tk_session_root():
    """One hidden Tk interpreter for the whole run. Creating and destroying many Tk() roots in one process
    makes Tk fail intermittently ("invalid command name tcl_findLibrary"), so window tests share this one."""
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def tk_window(_tk_session_root):
    """A fresh hidden window (Toplevel) on the shared root, removed after the test."""
    window = tk.Toplevel(_tk_session_root)
    window.withdraw()
    yield window
    window.destroy()
