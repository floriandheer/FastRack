# WordPress Dev Backup

Back up and restore full local WordPress development sites (files + MySQL database) from the Laragon `www` folder, plus the Laragon environment itself. Designed for quick "roll back a day" recovery when something breaks during local development.

## Overview

Each per-site backup contains the **site's files + a full SQL dump** in a single zip. These backups are portable — nothing in them is Laragon-specific. You can drop the contents onto any LAMP/LEMP host, import the `.sql`, tweak `wp-config.php`, and the site runs.

The Laragon environment (`etc/` and `usr/`) is backed up separately, as a distinct concern — it captures Apache/Nginx vhosts, SSL certs, PHP config, and Laragon settings for re-provisioning a new PC.

### How it fits alongside the other Web tools

| Module | What it backs up | Format |
|---|---|---|
| **Laragon Workspace Manager** | Junction-linked project folders (files only, no DB) | `dev_<project>_*.zip` |
| **Publish Static Site** | Published static-site exports sent to FTP | `pub_<site>_*.zip` (on FTP) |
| **WordPress Dev Backup** (this) | WP site files + database, Laragon env | `dev_<site>_*.zip`, `env_laragon_*.zip` |

## Prerequisites

- **Laragon** installed (default: `C:\laragon`, configurable).
- **MySQL running** inside Laragon — the tool connects to `localhost` via `mysqldump`/`mysql` CLI.
- **Sites served from Laragon www** — can be regular folders or junctions to a work drive; both are handled transparently.
- WordPress credentials are parsed automatically from each site's `wp-config.php`, so the tool always uses current values.

## How sites are discovered

On refresh, the tool scans the Laragon `www` directory and picks up any folder containing a `wp-config.php` at its root. For each discovered site it parses:

- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`
- `$table_prefix`

Folders without a `wp-config.php` (static sites, DokuWiki, VitePress projects, etc.) are skipped — those are handled by the other Web tools.

## Usage

### Back up a single site

1. Launch **FastRack → Web → WordPress Dev Backup**.
2. Select a site in the list.
3. Click **Backup Selected**.
4. The tool runs `mysqldump` on the site's DB, then zips site files + SQL dump into `<backup root>\<site>\dev_<site>_YYYY-MM-DD_HHMMSS.zip`.
5. Old backups beyond the retention limit are rotated out.

### Back up everything

Click **Backup All** — runs the flow above for every discovered WP site in sequence.

### Restore a site

1. Select the site, click **Restore Selected**.
2. Pick a backup from the list (sorted newest-first, with size + timestamp).
3. Confirm the destructive action.
4. The tool imports the SQL dump first (so if the DB fails, files aren't touched), then overlays the site files.

> **Note**: Restore is an *overlay* — it replaces existing files but doesn't delete new files added since the backup. The database, however, is fully replaced (the dump uses `--add-drop-database`).

### Back up the Laragon environment

Click **Backup Environment** — zips `<laragon>\etc` (Apache/Nginx vhosts, SSL certs, PHP configs) and `<laragon>\usr` (laragon.ini, user scripts) into `<backup root>\_env\env_laragon_YYYY-MM-DD_HHMMSS.zip`.

Use this after adding a new virtual host, installing an SSL cert, or tweaking PHP settings.

### Restore the environment

1. **Stop Laragon first.**
2. Click **Restore Environment**, pick a snapshot, confirm.
3. Restart Laragon.

## Storage layout

Default backup root: `<archive path>\Web\_DevBackups\` (the archive path comes from your FastRack settings; can be overridden in the settings dialog).

```
_DevBackups\
  alles3d\
    dev_alles3d_2026-04-14_1530.zip
    dev_alles3d_2026-04-13_1820.zip
  floriandheer\
    dev_floriandheer_2026-04-14_1532.zip
  hyphen-v\
    dev_hyphen-v_2026-04-14_1534.zip
  _env\
    env_laragon_2026-04-14_1535.zip
```

### Archive contents

Each `dev_<site>_*.zip` contains:

```
__db__.sql          ← mysqldump output (with DROP + CREATE DATABASE)
files/
    index.php
    wp-config.php
    wp-content/
    ...
```

Each `env_laragon_*.zip` contains:

```
etc/
    apache2/sites-enabled/*.conf
    nginx/sites-enabled/*.conf
    ssl/*.crt, *.key, *.pem
    php/php-*/
usr/
    laragon.ini
    user.cmd
    ...
```

## Configuration

Config file:
```
%LOCALAPPDATA%\PipelineManager\web_devbackup_config.json
```

Fields:

| Key | Default | Meaning |
|---|---|---|
| `laragon_path` | `C:\laragon` | Root of Laragon install |
| `backup_root` | `<archive>\Web\_DevBackups` | Where backups go |
| `backup_max_per_site` | `5` | Rotation limit per site |
| `backup_max_env` | `3` | Rotation limit for env backups |
| `backup_exclude_dirs` | `.git, node_modules, vendor, __pycache__, .cache, .tmp` | Dirs skipped when zipping files |

All configurable via **Settings** in the UI.

## Security notes

- Database passwords from `wp-config.php` are **never passed on the command line**. They're written to a temp `--defaults-extra-file`, used by `mysqldump`/`mysql`, then deleted in a `finally` block. This prevents passwords from showing up in process lists.
- The SQL dump inside the zip *does* contain the DB schema and data in plain text. Treat backup zips with the same care as the site files themselves.

## Troubleshooting

**"mysqldump.exe not found under C:\laragon\bin\mysql"**
- Check the Laragon install path in Settings.
- Verify MySQL is installed inside Laragon at `<laragon>\bin\mysql\<version>\bin\`.

**"mysqldump failed: Access denied"**
- Check `DB_USER` / `DB_PASSWORD` in the site's `wp-config.php` match the actual MySQL credentials.
- Make sure MySQL is running in Laragon.

**"mysqldump failed: Unknown database"**
- The `DB_NAME` in `wp-config.php` doesn't exist on the MySQL server. Create it in Laragon's HeidiSQL/phpMyAdmin or fix the config.

**Restore fails with "Access denied" on files**
- Stop Laragon / Apache / editors that may have locks on site files, then try again.

**Site list is empty**
- Check the Laragon www path in Settings.
- Verify at least one site folder contains `wp-config.php` at the root (discovery follows Windows junctions transparently).

**Env restore didn't take effect**
- Laragon must be **fully stopped** before restoring env files — running services hold file locks.
- Restart Laragon after restore.

## Design notes

- **Per-site DB dump vs. whole MySQL data dir**: Dumping per database is cleaner, portable, smaller, and doesn't require stopping MySQL. The raw data directory is intentionally *not* backed up.
- **SQL imported before files overlay**: If the DB import fails, the site files on disk are untouched — nothing is partially restored.
- **`--add-drop-database` in the dump**: Restore guarantees a clean DB state, even if tables were added or altered since the backup.
- **Credentials re-read every run**: The tool re-parses `wp-config.php` on each discovery, so password changes are picked up automatically without reconfiguring anything.
