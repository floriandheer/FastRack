# Changelog

All notable changes to FastRack will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
