# Quick Start Guide

Get up and running with FastRack in 5 minutes.

## 1. Run the installer (3 minutes)

From the project directory:

```bash
python install.py
```

Press Enter to begin and follow the prompts. The installer covers:

- Python packages
- External tools (FFmpeg, FLAC, rclone) via winget
- Folders + drive mappings (`subst I:`, `subst P:`, …)
- Pipeline config (`rack_config.json`)
- A pinnable `Fastrack.lnk` shortcut
- A final "doctor" pass that confirms everything is healthy

Safe to re-run any time. Press `Ctrl+C` at any prompt to bail.

## 2. Launch the application (10 seconds)

**Windows:** double-click `Fastrack.lnk` (or right-click → Pin to taskbar).

**Any platform:**
```bash
python fastrack_hub.py
```

## 3. Use your first script (1 minute)

1. **Select a category** from the top tabs (Audio, Photo, Visual, Web, etc.)
2. **Click a script** from the left sidebar
3. **Click "Run Script"** at the bottom
4. **Follow the prompts** in the script output area

## Common first scripts

### Create a new project folder

1. Go to the relevant category (Audio DJ, Photo, Visual CG, …)
2. Click "New [Category] Project"
3. Choose a location when prompted
4. Enter a project name
5. Done — your folder structure is created

### Backup MusicBee library

1. **Audio** tab → **Backup MusicBee to OneDrive** → **Run Script**

### Rename invoices

1. **Business** tab → **Invoice Renamer** → **Run Script** → pick the folder

## Customization

### Change base paths

Edit the values in `setup_config.json` and re-run `python install.py --step env`. See [CONFIGURATION.md](CONFIGURATION.md) for more.

## Troubleshooting

### Script won't run?
- Check that the path in the script exists
- Verify you have write permissions
- Look at the output area for error messages

### Missing dependencies?
- Re-run `python install.py` — the doctor step lists exactly what is missing
- Or just the Python piece: `python install_dependencies.py`
- See [INSTALLATION.md](INSTALLATION.md) for manual install

### Drive letters not mapping after reboot?
- Re-run `python install.py --step env` — it writes a `HKCU\…\Run` entry so `subst` runs on login

### Can't find a script?
- Check it's in the correct category tab
- Some scripts are in subcategories (expand the section)

## Next steps

- Read the full [README.md](../README.md)
- Explore [CONFIGURATION.md](CONFIGURATION.md) for customization
- Press **F1** in the app to see every keyboard shortcut
- Press **Ctrl+,** to open Settings (paths, dependencies, shortcut)

## Help

All scripts have descriptions. Hover or click to see what they do before running.

---

**That's it. You're ready to use FastRack.**
