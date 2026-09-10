# Installation Guide

## TL;DR

```bash
git clone https://github.com/floriandheer/FastRack.git
cd FastRack
python install.py
```

That's it — `install.py` is a single command that takes a brand-new machine all the way to a working Pipeline Hub. The rest of this document explains what it does, the manual alternative, and troubleshooting.

## Prerequisites

### Python

1. **Download Python 3.8 or higher** from [python.org](https://www.python.org/downloads/)
2. During installation, **check "Add Python to PATH"**
3. Verify:
   ```bash
   python --version
   ```

### Platform notes

- **Windows** — recommended. Drive mappings, the desktop shortcut, and `winget`-powered tool installs all need native Windows (not WSL).
- **macOS / Linux** — the Python pipeline runs, but the environment-setup step (subst drives, registry persistence, Windows shortcut) is skipped automatically.

## Method 1 — Guided install (recommended)

```bash
python install.py
```

The installer walks you through six steps, asks before touching anything, and prints a clear "all green" report at the end.

| # | Step | What it does |
|---|------|--------------|
| 1 | Prerequisites | Checks Python version, pip, git, platform |
| 2 | Python packages | `pip install -r requirements.txt` |
| 3 | External tools | Detects FFmpeg / FLAC (metaflac) / rclone; offers `winget install` for any that are missing |
| 4 | Environment | Creates folder structure, maps `subst` drives with registry persistence, checks Synology Drive sync status, writes `rack_config.json` |
| 5 | Desktop shortcut | Generates `Fastrack.lnk` next to the script for taskbar pinning |
| 6 | Doctor | Verifies the end state: deps importable, config valid, paths reachable |

### Flags

| Flag | Description |
|------|-------------|
| `--yes` / `-y` | Accept every prompt (CI / unattended) |
| `--dry-run` | Show what would happen without changing anything |
| `--step STEP` | Run only one step: `prereq`, `deps`, `externals`, `env`, `shortcut`, `doctor` |
| `--skip-externals` | Skip the FFmpeg / FLAC / rclone step |
| `--skip-shortcut` | Skip the Windows shortcut creation |

### First-run on a new PC — what to expect

1. The installer runs to step 4 and notices there is no `setup_config.json`.
2. It copies `setup_config.json.example` to `setup_config.json` and (Windows) offers to open it in your default editor.
3. You edit drive letters and base paths, save, and re-run `python install.py`.
4. Steps 4–6 finish without any further prompts (other than confirmation).

## Method 2 — Manual / piecemeal

Each step also exists as a standalone script if you'd rather drive them yourself.

### Just the Python packages

```bash
pip install -r requirements.txt
```
or
```bash
python install_dependencies.py
```

The installer reads `requirements.txt` when present; falls back to a built-in list otherwise. Both support `--yes` for unattended runs.

### Just the environment

```bash
copy setup_config.json.example setup_config.json   # one time
# ...edit setup_config.json to match your machine...
python setup_environment.py                         # idempotent, safe to re-run
```

`setup_environment.py` accepts the same `--dry-run`, `--yes`, and `--step` flags described above (its own steps are `folders`, `drives`, `synology`, `config`).

### Just the shortcut

```bash
python make_shortcut.py
```

Resolves paths relative to the repo, so it works wherever the repo is cloned. Right-click the resulting `Fastrack.lnk` and choose **Pin to taskbar**.

## Virtual environment (advanced)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

python install.py
```

The installer always uses the active interpreter, so the venv is fully respected.

## External tools

Some pipeline scripts need command-line tools that pip can't install:

| Tool | Used by | Get it |
|------|---------|--------|
| **FFmpeg** | Audio conversion / format handling | [ffmpeg.org](https://ffmpeg.org/download.html) — `winget install Gyan.FFmpeg` |
| **FLAC** (`metaflac`) | Writing iTunes playlist metadata into FLAC tags | [xiph.org/flac](https://xiph.org/flac/download.html) — `winget install Xiph.Flac` |
| **rclone** | Cloud sync (OneDrive, Google Drive, …) | [rclone.org](https://rclone.org/downloads/) — `winget install Rclone.Rclone`, or drop `rclone.exe` into `tools/rclone/` |

`install.py --step externals` detects all three and offers a winget install on Windows.

After installing **rclone**, configure your remote once with `rclone config`.

## Drive mappings — how they work

`setup_environment.py` (and `install.py --step env`) uses the built-in Windows `subst` command:

```
subst I: "D:\_work\Active"
subst P: "D:\_work\_PIPELINE"
```

To make these survive a reboot, the script writes a per-user entry under:

```
HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
```

This does **not** require administrator rights and does not need VisualSubst or any third-party tool. If you want pretty drive labels in Explorer, [Visual Subst](https://www.ntwind.com/software/visual-subst.html) is a great optional add-on.

## Synology Drive

The installer **checks** whether Synology Drive is installed, running, and whether the expected sync folders exist and are populated. It **cannot** create sync tasks automatically (Synology Drive has no CLI). Missing tasks are listed as manual action items.

Download Synology Drive Client from [synology.com](https://www.synology.com/en-global/dsm/feature/drive).

## Idempotency

Every step is safe to re-run:

- Existing folders are skipped (`os.makedirs(exist_ok=True)`)
- Already-mapped `subst` drives are detected and left alone
- Registry values are only written when different from expected
- `RackSettings` merges config with defaults
- Synology checks are read-only
- `Fastrack.lnk` regenerates cleanly

## Troubleshooting

### `pyexiv2` installation fails

**Windows**
```bash
pip install pyexiv2 --only-binary :all:
```

**Linux**
```bash
sudo apt-get install libexiv2-dev libboost-python-dev
pip install pyexiv2
```

**macOS**
```bash
brew install exiv2 boost-python3
pip install pyexiv2
```

### `tkinter` not found

- **Windows / macOS** — bundled with Python by default
- **Linux** — `sudo apt-get install python3-tk`

### Permission denied

```bash
pip install --user -r requirements.txt
```

### Old pip

```bash
python -m pip install --upgrade pip
```

### winget not found

You're on an older Windows install or a stripped LTSC build. Install [App Installer from the Microsoft Store](https://apps.microsoft.com/detail/9NBLGGH4NNS1), or just download each tool from its homepage (see the table above).

### Doctor reports invalid paths

Open `setup_config.json`, fix the paths, then run `python install.py --step env` to re-apply.

## Verification

```bash
python install.py --step doctor
```

This re-runs only the final health check and reports anything still broken.

## Next steps

- [README.md](../README.md) — usage instructions
- [CONFIGURATION.md](CONFIGURATION.md) — customization options
- [QUICK_START.md](QUICK_START.md) — 5-minute first-run walkthrough
