#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runs a script inside its own console window (Windows) so its live output stays separate from other tasks.

    python shared_console_runner.py <title> <close_after_seconds> <script> [args...]

close_after_seconds: seconds the window stays after a successful run (a key press keeps it open);
0 = close immediately, negative = wait for a key press. A failed run always waits for a key press.
The exit code of the script is passed through.
"""

import subprocess
import sys
import time

try:
    import msvcrt
except ImportError:  # not Windows; the hub never uses this runner there
    msvcrt = None


def _set_title(title: str) -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def _wait_for_key() -> None:
    if msvcrt is not None:
        msvcrt.getwch()


def run(title: str, close_after: float, command) -> int:
    _set_title(title)
    print(f"=== {title} ===\n", flush=True)
    try:
        code = subprocess.call(command)
    except Exception as e:
        print(f"Could not start the task: {e}", flush=True)
        code = 1

    print(flush=True)
    if code != 0:
        print(f"FAILED (exit code {code}). Press any key to close this window.", flush=True)
        _wait_for_key()
    elif close_after < 0:
        print("Finished successfully. Press any key to close this window.", flush=True)
        _wait_for_key()
    elif close_after > 0:
        print(f"Finished successfully. Closing in {close_after:g}s - press any key to keep this window open.",
              flush=True)
        deadline = time.time() + close_after
        while time.time() < deadline:
            if msvcrt is not None and msvcrt.kbhit():
                msvcrt.getwch()
                print("Window kept open. Press any key to close it.", flush=True)
                _wait_for_key()
                break
            time.sleep(0.1)
    return code


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    title, close_after, script, *args = argv
    return run(title, float(close_after), [sys.executable, script, *args])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
