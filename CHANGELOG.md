# Changelog

All notable changes to FastRack will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
