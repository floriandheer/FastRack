# Laragon Workspace Manager

Manage WordPress development projects by linking Laragon's `www` folder to the organized work drive using Windows junctions.

## Overview

Instead of keeping all WordPress projects inside `C:\laragon\www`, this tool moves project files to the work drive (`I:\Web`) and creates Windows junctions so Laragon still serves them transparently. This makes the web dev environment fully portable - on a new PC, just install Laragon and recreate the junctions.

### Folder structure after setup

```
C:\laragon\www\
    floriandheer\  ->  junction to I:\Web\_Personal\floriandheer\02_Development
    hyphen-v\      ->  junction to I:\Web\_Personal\hyphen-v\02_Development
    alles3d\       ->  junction to I:\Web\_Personal\alles3d\02_Development
    {business}\    ->  junction to I:\Web\{business}\02_Development
```

Personal sites (floriandheer, hyphen-v, alles3d) go under `I:\Web\_Personal\{site}\02_Development`. Business projects go under `I:\Web\{project}\02_Development`.

## Prerequisites

- **Laragon** installed with projects in `C:\laragon\www`
- Work drive mapped (default: `I:`)
- No admin rights needed (junctions use `/J` flag)

## Usage

### First-time setup (linking projects)

1. Launch **FastRack > Web > Laragon Workspace Manager**
2. Verify the Laragon www path is correct (default: `C:\laragon\www`)
3. The project list shows all folders with their link status
4. Select an unlinked project and click **Link Project**
5. Choose the category (Personal or Business) - the target path is auto-computed
6. Click **Link** - the tool will:
   - Copy project files to the work drive
   - Remove the original folder
   - Create a junction pointing back to the work drive
7. Repeat for each project

### Verifying junctions

Click **Verify All** to check that all junctions are healthy and point to the correct targets. Green = healthy, red = problem.

### New PC workflow

When setting up a new PC where the work drive already has the project files:

1. Install Laragon
2. Launch the Workspace Manager
3. Click **Setup New PC**
4. The tool creates junctions for all configured projects

This requires the config file to exist (synced from the original PC or backed up).

## Configuration

Configuration is stored at:
```
%LOCALAPPDATA%\PipelineManager\laragon_config.json
```

Contains:
- `laragon_www_path` - path to Laragon's www directory
- `projects` - mapping of project names to their work drive targets and categories

## How junctions work

Windows junctions (created with `mklink /J`) are transparent directory links. Any application (including Laragon/Apache) that accesses `C:\laragon\www\floriandheer` will seamlessly read/write files at `I:\Web\_Personal\floriandheer\02_Development`. No admin rights are required to create junctions.

## Troubleshooting

**Project list is empty**
- Verify the Laragon www path is correct
- Check that Laragon is installed and has projects in the www folder

**Link fails: "Target already exists"**
- The destination on the work drive already has files. Remove or rename it first.

**Link fails: "Failed to remove original"**
- Close any applications (Laragon, editors, file explorer) that have files open in the project folder
- Try again

**Junction not accessible after creation**
- Check that the work drive is mounted
- Verify the target path exists on the work drive

**Setup New PC shows "No projects"**
- The config file needs to be present from the original PC
- Copy `%LOCALAPPDATA%\PipelineManager\laragon_config.json` from the original machine
