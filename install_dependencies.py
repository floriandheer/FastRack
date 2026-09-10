#!/usr/bin/env python3
"""
FastRack - Python Dependency Installer
----------------------------------------------------
Installs the Python packages required by the desktop pipeline. Reads from
requirements.txt if available; otherwise falls back to a built-in list.

Usage:
    python install_dependencies.py [--desktop-only] [--web-only] [--yes]

For a guided end-to-end first-time setup (deps + folders + drives + shortcut),
run `python install.py` instead.
"""

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


# ============================================================
# Pretty console output (stdlib-only, Windows-aware)
# ============================================================

def _supports_ansi() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        # Enable VT processing on Windows 10+; harmless if it fails.
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            return False
    return True


_USE_COLOR = _supports_ansi()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def bold(t): return _c("1", t)
def dim(t):  return _c("2", t)
def red(t):  return _c("31", t)
def grn(t):  return _c("32", t)
def ylw(t):  return _c("33", t)
def cyn(t):  return _c("36", t)


CHECK = grn("ok") if _USE_COLOR else "[ok]"
CROSS = red("xx") if _USE_COLOR else "[xx]"
ARROW = cyn(">>") if _USE_COLOR else ">>"
DOTS = dim("..")


# ============================================================
# Package definitions (fallback if requirements.txt missing)
# ============================================================

CORE_PACKAGES = [
    {"name": "Pillow", "pip_name": "pillow>=10.0.0", "description": "Image processing and logo display"},
]

DESKTOP_PACKAGES = [
    {"name": "pyexiv2", "pip_name": "pyexiv2>=2.8.0", "description": "EXIF metadata read/write for image scripts"},
    {"name": "setuptools", "pip_name": "setuptools<81", "description": "Required by invoice2data (pkg_resources)"},
    {"name": "pdfplumber", "pip_name": "pdfplumber>=0.9.0", "description": "PDF text extraction for invoice processing"},
    {"name": "invoice2data", "pip_name": "invoice2data>=0.4.0", "description": "Template-based invoice data extraction"},
    {"name": "pyyaml", "pip_name": "pyyaml>=6.0", "description": "invoice2data template loading (incoming_scanner.py)"},
]

WEB_PACKAGES: list = []


# ============================================================
# Helpers
# ============================================================

# pip name → top-level import name, for packages where they differ.
_PIP_TO_IMPORT = {
    "pillow": "PIL",
    "pyyaml": "yaml",
    "beautifulsoup4": "bs4",
    "python-dateutil": "dateutil",
    "opencv-python": "cv2",
}


def is_package_installed(pip_name: str) -> bool:
    """Check if a package is importable. Accepts the pip name with version pin."""
    base = pip_name.split(">=")[0].split("==")[0].split("<")[0].split("[")[0].strip()
    key = base.lower()
    import_name = _PIP_TO_IMPORT.get(key, key.replace("-", "_"))
    return importlib.util.find_spec(import_name) is not None


def install_package(pip_name: str) -> bool:
    """Install a single package via pip."""
    print(f"  {ARROW} {pip_name} {DOTS}")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pip_name],
            stdout=subprocess.DEVNULL if _USE_COLOR else None,
            stderr=subprocess.STDOUT,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"     {red('error:')} {e}")
        return False


def install_from_requirements(path: Path) -> bool:
    """Install everything from a requirements.txt."""
    print(f"  {ARROW} pip install -r {path.name}")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", str(path)]
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"     {red('error:')} {e}")
        return False


def confirm(prompt: str, auto_yes: bool) -> bool:
    if auto_yes:
        print(f"{prompt} {dim('(auto-confirmed via --yes)')}")
        return True
    return input(f"{prompt} (y/n): ").strip().lower() in ("y", "yes")


def banner(title: str):
    line = "=" * 60
    print()
    print(bold(line))
    print(bold(f"  {title}"))
    print(bold(line))


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Install FastRack Python dependencies",
    )
    parser.add_argument("--web-only", action="store_true", help="Install only web-interface deps")
    parser.add_argument("--desktop-only", action="store_true", help="Install only desktop-app deps")
    parser.add_argument("--skip-optional", action="store_true", help="Skip optional packages")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")
    parser.add_argument(
        "--no-requirements", action="store_true",
        help="Ignore requirements.txt and use the built-in package list",
    )
    args = parser.parse_args()

    banner("FastRack - Python Dependencies")
    print(f"  Python:    {sys.version.split()[0]}  ({sys.executable})")
    print(f"  Platform:  {sys.platform}")

    requirements = Path(__file__).resolve().parent / "requirements.txt"
    use_requirements = (
        not args.no_requirements
        and not args.web_only
        and not args.desktop_only
        and requirements.exists()
    )

    if use_requirements:
        print(f"  Source:    {requirements.name} ({len(requirements.read_text().splitlines())} lines)")
        print()
        if not confirm(f"  Install everything from {cyn(requirements.name)}?", args.yes):
            print(dim("  Skipped."))
            return
        ok = install_from_requirements(requirements)
        print()
        if ok:
            print(f"  {CHECK} All packages from {requirements.name} installed.")
        else:
            print(f"  {CROSS} Some packages failed. Re-run with --no-requirements to try one by one.")
        _print_next_steps(args)
        return

    # --- Fallback: built-in list ---
    packages = list(CORE_PACKAGES)
    if args.web_only:
        packages.extend(WEB_PACKAGES)
        print(f"  Mode:      web-only")
    elif args.desktop_only:
        packages.extend(DESKTOP_PACKAGES)
        print(f"  Mode:      desktop-only")
    else:
        packages.extend(DESKTOP_PACKAGES)
        packages.extend(WEB_PACKAGES)
        print(f"  Mode:      full install (desktop + web)")

    if args.skip_optional:
        packages = [p for p in packages if not p.get("optional", False)]

    print()
    print(bold("  Checking packages:"))

    missing = []
    for pkg in packages:
        status = CHECK if is_package_installed(pkg["pip_name"]) else CROSS
        print(f"  {status} {pkg['name']:<14} {dim(pkg['description'])}")
        if status == CROSS:
            missing.append(pkg)

    if not missing:
        print()
        print(f"  {CHECK} All packages already installed.")
        _print_next_steps(args)
        return

    print()
    print(f"  {ylw('Missing:')} {len(missing)} package(s)")
    for pkg in missing:
        print(f"    - {pkg['name']} ({pkg['pip_name']})")

    print()
    if not confirm("  Install missing packages now?", args.yes):
        print(dim("  Skipped. The app may not run correctly."))
        return

    print()
    failed = []
    for pkg in missing:
        if install_package(pkg["pip_name"]):
            print(f"     {CHECK} {pkg['name']}")
        else:
            failed.append(pkg)
            print(f"     {CROSS} {pkg['name']}")

    print()
    if failed:
        print(f"  {ylw('Warning:')} {len(failed)} package(s) failed:")
        for pkg in failed:
            print(f"    - {pkg['name']}")
        print(f"  {dim('Try: pip install --upgrade pip, then re-run.')}")
    else:
        print(f"  {CHECK} All packages installed.")

    _print_next_steps(args)


def _print_next_steps(args):
    banner("Next Steps")
    if not args.web_only:
        print(f"  {ARROW} Launch the hub:")
        print(f"     python fastrack_hub.py")
        print()
    if not args.desktop_only and WEB_PACKAGES:
        print(f"  {ARROW} Start the web interface:")
        print(f"     python web_pipeline_server.py")
        print(f"     {dim('then open http://127.0.0.1:8000')}")
        print()
    print(f"  {ARROW} First-time on a new PC? Run the full setup:")
    print(f"     python install.py")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print(red("  Installation cancelled."))
        sys.exit(1)
    except Exception as e:
        print()
        print(red(f"  Unexpected error: {e}"))
        print(dim("  Try re-running with administrator privileges, or install manually:"))
        print(dim("    pip install -r requirements.txt"))
        if os.name == "nt" and not sys.stdin.isatty():
            input("\nPress Enter to exit...")
        sys.exit(1)

    if os.name == "nt" and not sys.stdin.isatty():
        input("\nPress Enter to exit...")
