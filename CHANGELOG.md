# Changelog

All notable changes to FastRack will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Environment Setup opens the Synology Drive download page** — once the Folders and/or Drives
  steps (or Run All) have finished without errors, `setup_environment.py` opens
  https://www.synology.com/nl-nl/dsm/feature/drive in the browser, on Windows and macOS, so the
  Synology Drive Client can be installed next. It only opens when the client is not installed yet
  (looked for under Program Files / AppData on Windows and in Applications on a Mac), only once per
  run, and never in a dry run. The confirmation dialogs mention it, and the Synology step's hints
  now point to the same page.
- **Environment Setup shows which steps still need action** — in Settings > General > Environment
  Setup, a line per step sits under the buttons: amber with a dot when the step still has something to
  do (missing folders, a drive mapping that is not active, a scheduled task that is not registered,
  ...) and what is missing, green with a tick when it is done, dim when it does not apply on this
  system (the Windows-only steps on a Mac). The buttons themselves are unchanged. The check only
  looks (it never creates anything), runs in the background, and re-runs when you come back to the
  dialog after starting a step, or with the new Refresh button.
  The checks live in `modules/environment_status.py`; the Folders step now uses the same folder plan.
- **Traktor Stems** (DJ Tools) — everything about Traktor's stems in one window. A status panel
  compares the "has stems" link on each track in `collection.nml` (bit `0x40` of `INFO FLAGS`)
  with the stem files actually on disk, and the tabs follow the life of a stem:
  - **Create stems** (macOS) — pick a playlist; it presses Traktor's Shift+S for each track
    (Down arrow in between) and waits for the "Finalized stem separation process" line in
    Traktor's log instead of a fixed delay. Tracks that already have stems, or got them in a
    recent run, are stepped over, so an interrupted run resumes. It stops if Traktor reports a
    different track than expected (browser not in playlist order), if no result arrives in
    time, or if Traktor loses focus or quits; failed tracks are listed. Keeps the Mac awake.
  - **Re-link** — sets the link again on tracks whose stem file still exists. Traktor's logs say
    which track each stem file was made for, a collection backup says which tracks had the link;
    titles shared by several tracks or no longer in the collection are skipped and listed.
  - **Clean up** — remove the links (only those without a stem file, or all), delete the stem
    files (to the Trash through Finder on a Mac, or permanently), or both at once in the safe
    order (links first). Only `*.stem.mp4` files of the exact shape Traktor writes are ever listed.
  Every change to `collection.nml` makes a timestamped backup first and needs Traktor closed;
  the command line (`status`, `playlists`, `create`, `relink`, `unlink`, `delete`, `clean`)
  only previews unless `--apply` is given.
- **One-click tasks run in their own console window** — on Windows, a hub
  button that runs a task directly (Sync to Traktor, Sync Traktor Playlists)
  now opens a dedicated, titled console window with that task's live
  progress, so tasks no longer share one log. The window closes 10 seconds
  after a successful run (press a key to keep it) and stays open on an error
  until a key is pressed; the hub status line still reports started/finished.
  Both tasks first state whether they are replaying an export or an import
  (the mode last used), since an import changes Traktor's `collection.nml`.
  Change `CONSOLE_CLOSE_AFTER_SECONDS` in `modules/ui_script_runner.py`
  (0 = close immediately, negative = always wait).
- **Sync to Traktor direct run now logs its full progress** — every step line
  that previously only went to the hidden window (copying, conversions,
  kept/moved files, the playlist doctor's findings) is also written to the
  task's log and console. In a direct run the playlist doctor never opens a
  window: it resolves dead entries for that sync only and logs them.
- **MusicBee Cleanup** — new standalone Audio tool (no other tool or service
  needed) to keep MusicBee playlists clean. Scans the playlist files against
  MusicBee's iTunes XML and lists, per playlist: dead entries (replaced with
  the real library track when exactly one matches), the same file listed more
  than once (extra copies removed), and the same song present as several
  different files (you choose which to drop; at least one always stays).
  Safe fixes start ticked, anything that removes a song starts unticked.
  Applying makes a backup of every changed playlist first, refuses while
  MusicBee is running, and only ever edits playlist files. The Library tab
  reports tracks whose file is missing and songs that exist as several files
  (report only, CSV export); music files and MusicBee's library database are
  never touched.
- **MusicBee playlist doctor** — Traktor Sync and PowerAmp Sync now check the
  selected MusicBee playlists before syncing. Playlist entries that still point
  at the Soulseek staging folder (moved to the library since) are invisible in
  MusicBee's iTunes XML, so those tracks silently missed their playlists. The
  doctor matches each dead entry to the real library track by artist and title
  (only when exactly one track matches) and includes it for this sync; an
  optional "Repair playlists" button rewrites the `.mbp` files (backup in
  `%LOCALAPPDATA%\PipelineManager\playlist_backups`, refused while MusicBee is
  running). A "Check Playlists" button runs the same scan on demand, and
  `python modules/shared_musicbee_playlists.py scan|repair [--apply]` does it
  from the command line (dry run by default). New settings:
  `playlist_doctor_enabled`, `musicbee_playlists_dir`, `staging_roots`.
- **Traktor Sync keeps what Traktor still uses** — the "Delete removed tracks"
  step no longer deletes a DJ Library file that a Traktor playlist (e.g.
  "Preparation") still references, and moves everything else to
  `DJ Library\_Removed\<date>` instead of deleting it. Files a Traktor playlist
  references that are missing on disk (for example after a track rolled out of
  "Recently Added") are restored from MusicBee on the next sync. New settings:
  `protect_traktor_referenced`, `quarantine_removed`, `traktor_collection_path`.
- **Open in Darktable** — new Photo project action (first in the list, above
  Open export in IrfanView and RAW Cleanup) that imports the pictures directly in the
  project's root folder into Darktable — no subfolders, so `_export` stays
  out of the library — and shows them in the lighttable. Done with a small
  Lua script (`--luacmd`) because `darktable <folder>` crashes on
  Darktable 5.2.1 and only opens a collection without importing. Recursive
  import is switched off for that run only (`--conf`, not saved). Darktable
  allows one instance per library, so a message asks you to close it first
  if it is already open.
- **Open export in IrfanView** — new Photo project action (above RAW
  Cleanup, in both the project Actions panel and the project deck) that
  opens the first picture of the project's `_export` folder in IrfanView
  (the rest of the folder is then one arrow key away). IrfanView is located
  via PATH, its install folder, or the registry, and a message box explains
  if the folder is missing, has no pictures, or IrfanView isn't installed.
- `install.py` — a friendly first-run installer that takes a fresh machine
  to a working Pipeline Hub in six guided steps: prerequisites, Python
  packages, external tools (FFmpeg / FLAC / rclone via winget),
  environment (folders + drives + config), desktop shortcut, and a final
  doctor health check. Every step is idempotent and asks before touching
  anything. Use `--yes` for unattended, `--dry-run` to preview, or
  `--step STEP` to run a single phase.
- `requirements.txt` — single source of truth for Python deps. Both
  `install.py` and `install_dependencies.py` read it.
- `install_dependencies.py` now installs `pyexiv2` (previously listed in
  the README but missing from the installer) and uses
  `requirements.txt` when present.

### Fixed
- **Two-finger trackpad scrolling now works everywhere, not just in the left column** — with
  Tk 9 (as in the Python 3.14 install on the Mac) a trackpad swipe is no longer sent as
  `<MouseWheel>`, so every scrollable area that only listened for the mouse wheel ignored it: the
  Project Finder's grid view, the Sandbox browser's grid, and the Paths, Software Defaults,
  Drives, Startup Apps, Workstation Apps and Folder Templates tabs in Settings. They now scroll
  with the trackpad like the left column does (a real mouse wheel still works, and Tk 8.6 builds
  such as python.org's on Windows are unaffected). Wheel scrolling in Settings also no longer
  rounds small wheel steps down to nothing. The Project Finder grid also keeps scrolling after
  you have used the left column (picking a category used to remove its global wheel binding, so
  scrolling stopped working until FastRack was restarted); it is now bound on the window instead.
  The shared logic is in `shared_scrollable_frame.py` (`enable_canvas_scroll` for hand-built
  canvases).
- **FastRack logo now shows on macOS, in the header and as the app icon** — two separate causes.
  The header logo was missing whenever FastRack was started from `Fastrack.app` (Finder/Dock): the
  bundle's launcher script was run under Rosetta (x86_64), so the arm64 Pillow install failed to
  import and the header fell back to the "FD" text (terminal launches were fine). `make_shortcut.py`
  now pins the launcher to the architecture the packages were installed for (`arch -arm64`), and the
  log message now says why Pillow could not be imported. The app icon was missing because the bundle
  had no icon and the Dock showed Python's rocket: `make_shortcut.py` now builds
  `Resources/Fastrack.icns` from the new `assets/Fastrack_AppIcon.png` (the white logo on the theme's
  dark background, generated by `make_app_icon.py`), and the hub sets the same image as the Dock icon
  at startup. Re-run `python3 make_shortcut.py` once to regenerate `Fastrack.app`.
- **Environment Setup steps now open a Terminal window on macOS** — the buttons under Settings >
  General > Environment Setup started `setup_environment.py` as a background process on a Mac, where
  it has no usable stdin, so its yes/no questions ("Create these directories?") could not be answered.
  They now open a Terminal window, like the Workstation Apps installer does. Windows is unchanged.
- **Project Finder no longer freezes when you drag the grid-size slider on macOS** — the slider's
  command rebuilt the whole grid from inside the slider's own redraw; the rebuild ends in
  `update_idletasks()`, which redraws the slider, which ran the command again, without end. The grid is
  now rebuilt from the event loop, once per burst of slider movement (the setting is saved once too),
  and a rebuild that is requested while another one is running waits for it instead of starting inside
  it. Tests: `tests/test_project_explorer_grid.py`.
- **Project Finder finds and keeps projects on macOS** — its import scan built every folder as
  `<active base>\Visual` with a literal backslash, so none of its 18 active and 16 archive folders
  existed on a Mac and a refresh imported nothing; and the project database turned every `/` of a
  stored path into `\`, so even a found project would have been saved as a path that exists nowhere.
  Folders are now built with the system's separators and native macOS/Linux paths are stored as they
  are. WSL (`/mnt/d/...`) and Windows paths are handled exactly as before. Tests:
  `tests/test_project_paths_platform.py`.
- **Software and Launchers paths on macOS follow the Windows layout** — on Windows `P:` is
  `D:\_work\_PIPELINE` and the NAS "work" sync brings that folder to `~/_work`, so the Mac defaults are
  now `~/_work/_PIPELINE/Software` and `~/_work/_PIPELINE/Launchers` instead of the folders
  `~/_work/Software` and `~/_work/Launchers` that nothing created (which made the status log report two
  unreachable paths at every start). A config that still holds those old defaults is moved over once
  when it is loaded (logged, backup kept next to it), unless the old folder really exists.
- **Drive and path settings now work on macOS/Linux** — `subst` drive letters (`I:`, `M:`, `P:`, `F:`)
  only exist on Windows, but a new `rack_config.json` on a Mac was seeded from `setup_config.json`'s
  Windows values (`I:`, `D:\_work\Active`, `P:\Software`, ...). Every path then came out relative to the
  current folder (`I:/Visual`, `D:\_work\Active/Visual`), validation reported "Drive/path not found"
  everywhere, and literal `D:\_work\Active` folders could be created inside the repository.
  - The first-run seed ignores drive-letter and backslash values off Windows, so the platform defaults
    (`~/_work/Active`, `~/_work/Archive`, ...) stay in place.
  - An existing `rack_config.json` that already holds such values is repaired once when it is loaded off
    Windows: the drive, archive, work and software-sync paths fall back to the platform defaults (the work
    root follows the active base), the change is logged, and the old file is copied next to it as
    `rack_config.json.bak-windows-paths-<timestamp>` first. Nothing is deleted.
  - `get_work_path`, `get_active_path` and `get_archive_path` no longer return a relative or
    backslash path off Windows; a bad base falls back to the platform default.
  - Settings > Drives shows a note on macOS/Linux that `subst` letters are Windows-only, and the
    "Run Drives step now" button is disabled there instead of doing nothing.
  Windows behaviour is unchanged. Tests: `tests/test_rack_settings_platform.py`.
- **Traktor Playlist Sync import no longer copies stem links to the other machine** — tracks
  it adds to the destination's collection now have the stem bit cleared, since the stem files
  stay behind on the machine that generated them.
- **Coloured buttons are no longer plain white boxes on macOS** — on a Mac, a classic `tk.Button`
  is drawn as a native control that ignores its background and text colour, so a green or blue
  button showed up as a white rectangle. Every coloured button now goes through
  `make_button()` (new, in `shared_color_button.py`), which gives the stock Tk button on Windows
  and Linux exactly as before and a `ColorButton` on macOS. Fixed in the Rack Settings dialog,
  Sandbox browser, Project deck, Invoice Manager (primary/secondary/danger buttons and the debug
  banner), Dev Backup, Laragon Workspace, Resolume Sync, Publish Static, Publish to Webshop,
  PowerAmp Sync, Software Launcher, Software Config Sync, Format Genres, Format Titles and the
  shared styled form button. The list/grid view toggles in Project Tracker and the Sandbox
  browser (`make_toggle_button()`) show their blue selected state again on a Mac.
  `ColorButton` now also supports `relief`, `borderwidth`, `activebackground`/`activeforeground`
  hover colours, `disabledforeground`, `invoke()`, `flash()`, `config(command=...)` and a
  keyboard focus ring. A new test (`tests/test_color_button.py`) fails if a coloured raw
  `tk.Button` is added again.

### Changed
- Renamed the project from FastRak to **FastRack** throughout code, docs,
  and file names — `fastrak_hub.py` → `fastrack_hub.py`,
  `fastrak_project_explorer.py` → `fastrack_project_explorer.py`,
  `modules/rak_settings.py` → `modules/rack_settings.py`
  (`RakSettings` → `RackSettings`, `rak_config.json` → `rack_config.json`).
  Generic/outdated product titles ("Florian Dheer Pipeline", "Pipeline
  Manager") were unified under the single **FastRack** name, including the
  in-app `APP_NAME`, window title, and taskbar `AppUserModelID`
  (`floriandheer.fastrak` → `floriandheer.fastrack`). Existing pinned
  shortcuts should be regenerated via `python make_shortcut.py`.
- README, `docs/QUICK_START.md`, and `docs/INSTALLATION.md` restructured
  around `python install.py` as the single entry point. Removed the
  stale `fastrack_launcher.vbs` reference (the file never existed).
- Folder structure creators consolidated into a single manifest-driven
  `GenericFolderStructureCreator`. Adding a new project subtype is now a
  one-entry change in `pipeline_categories.CATEGORIES`. Outliers (Photo,
  Physical) are handled by small extension classes in
  `modules/folder_structure_extensions/`.
- All category metadata (colors, emojis, display names, subtypes, menu
  scripts, legacy project_type aliases) consolidated into a single nested
  `CATEGORIES` dict in `modules/pipeline_categories.py`. Adding or editing
  a category is now a one-place change; everything else (registry, color
  table, archive routing, menu tree, project_type lookup) is derived.
- Audio folder-creator subtype renamed `Audio` → `PROD`; new projects write
  `project_type="Audio-Production"`. Legacy DB rows with `"Audio"` resolve
  via the alias index.
- Audio `DJ` is now a registered subtype carrying its own menu scripts.

### Removed
- 9 legacy per-subtype creator scripts in
  `modules/PipelineScript_*_FolderStructure*.py`.
- `modules/folder_structure_manifest.py` (data moved into `pipeline_categories.py`).
- Inline `CATEGORY_COLORS`, `PROJECT_TYPES`, `ARCHIVE_CATEGORIES` definitions
  in `fastrack_project_explorer.py` and the duplicate `CATEGORY_COLORS` in
  `ui_theme.py`. All now derive from `pipeline_categories.CATEGORIES`.

## [0.5.0] - 2025-01-27

### Added
- Professional project structure with proper documentation
- Comprehensive README.md with installation and usage instructions
- .gitignore file for version control
- LICENSE file
- CHANGELOG.md for version tracking
- docs/, tests/, and config/ directories for better organization
- Documentation: INSTALLATION.md, CONFIGURATION.md, CONTRIBUTING.md, QUICK_START.md
- Organized assets into dedicated folder

### Changed
- Updated requirements.txt to include missing pyexiv2 dependency
- Removed unused web framework dependencies (FastAPI, uvicorn, etc.)
- Cleaned up requirements to only include actively used packages
- Moved logo and favicon to assets/ directory
- Reorganized project to follow Python best practices

### Fixed
- Missing pyexiv2 dependency that caused metadata scripts to fail

## [0.4.0] - 2024-10-18

### Added
- Professional dark-themed UI
- Category-based script organization
- Multi-threaded script execution
- Enhanced error handling and logging

### Changed
- Reorganized scripts into category-based structure
- Improved user experience with better visual feedback

## [0.3.0] - 2024-09-03

### Added
- Initial pipeline manager with GUI
- Basic script launcher functionality
- Core pipeline scripts for various workflows
- Enhanced dependency installer

---

## Version Numbering

- **Major version** (X.0.0): Incompatible API changes or major redesigns
- **Minor version** (0.X.0): New features, backwards compatible
- **Patch version** (0.0.X): Bug fixes, backwards compatible
