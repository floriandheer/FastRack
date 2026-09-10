# FastRack Keyboard Navigation System

This document describes the keyboard shortcuts and navigation philosophy for FastRack.

---

## Shortcut Philosophy

### Design Principles

The FastRack keyboard navigation system is designed around these core principles:

#### 1. **Two-Hand Efficiency**
- **Left hand (WASD)**: Controls WHERE you are (panel/section navigation)
- **Right hand (Arrows)**: Controls WHAT you're selecting (item navigation within panels)
- This mirrors gaming conventions where movement and actions are split between hands

#### 2. **Direct Access Over Navigation**
- **Number keys (1-6)** provide instant filter changes without navigating to filter controls
- **Shift+Letter** provides instant category selection without grid navigation
- Philosophy: If you know what you want, you shouldn't have to navigate to get there

#### 3. **Context-Sensitive Behavior**
- Same keys behave appropriately based on current context
- Arrows in a grid (Categories) = 2D navigation
- Arrows in a list (Tools) = 1D navigation
- Arrows in Tracker = Delegated to tracker's own navigation

#### 4. **Mnemonic Shortcuts**
- `G` = **G**o to folder (open directory)
- `N` = **N**otes
- `V` = **V**isual, `R` = **R**ealTime, `A` = **A**udio, etc.
- `H` = P**h**oto (since P is taken by Physical)

#### 5. **Escape to Close**
- Escape closes the project creation panel if open
- Consistent mental model: "Escape closes dialogs"

#### 6. **Non-Destructive by Default**
- Navigation and selection are separate from execution
- Enter/Space activates, arrows just move focus
- You can explore freely without triggering actions

---

## Shortcut Layers

```
Layer 1: Global Filters (always work)
├── 1/2/3 = Scope (Personal/Work/All)
├── 4/5/6 = Status (Active/Archive/All)
└── Shift+Letter = Category quick select

Layer 2: Panel Navigation (WASD)
├── W/S = Move up/down through panels
└── A/D = Switch left panel ↔ project tracker

Layer 3: Item Navigation (Arrows + Enter)
├── Arrows = Navigate within current panel
└── Enter = Activate/select current item

Layer 4: Quick Actions (context-sensitive)
├── Ctrl+N = New project (requires selected category)
├── G or 0 = Open folder (requires selected category)
├── N or . = Open notes (requires selected category)
├── / or Ctrl+F = Focus search
├── Ctrl+D = Open project deck (requires selected project)
├── ` = Cycle scope
└── Esc = Close creation panel
```

---

## Complete Shortcut Reference

### Global Shortcuts

| Keys | Action |
|------|--------|
| **F1** | Open this help documentation |
| **F5** | Refresh projects (reimport all) |
| **F11** | Toggle fullscreen |
| **Ctrl+N** | New project (requires selected category) |
| **Ctrl+,** | Open settings |
| **Ctrl+L** | Open logs folder |
| **Ctrl+F** | Focus search field |
| **Ctrl+D** | Open quick-actions deck for the selected project |
| **T** | Toggle project tracker between list and grid view |
| **Esc** | Close project creation panel |

### Scope Filters (affects both categories and projects)

| Keys | Action |
|------|--------|
| **1** | Personal projects only |
| **2** | Work/Client projects only |
| **3** | All projects |
| **`** | Cycle through scopes |

### Status Filters (affects project list)

| Keys | Action |
|------|--------|
| **4** | Active projects only |
| **5** | Archived projects only |
| **6** | All projects |

### Panel Navigation (WASD)

| Keys | Action |
|------|--------|
| **W** | Move focus up (Tools → Operations → Categories) / From tracker: go to Categories |
| **S** | Move focus down (Categories → Operations → Tools) / From tracker: go to Tools |
| **A** | Move focus to last selected left panel (from tracker) |
| **D** | Move focus to project tracker |

**Note:** When in the Project Tracker: W goes to Categories (top), S goes to Tools (bottom), and A returns to the last selected left panel.

### In-Panel Navigation (Arrows)

| Panel | Up/Down | Left/Right | Enter |
|-------|---------|------------|-------|
| **Categories** | Move by row (±2) | Move by 1 | Auto-selects |
| **Operations** | - | Move by 1 | Auto-selects |
| **Tools** | Move through list | - | Run tool |
| **Tracker (grid)** | Move by row | Move by 1 | Open project folder |
| **Tracker (list)** | Move 1 row | — | Open project folder |

### Category Quick Select (Shift+Letter)

| Keys | Category |
|------|----------|
| **Shift+V** | Visual |
| **Shift+R** | RealTime |
| **Shift+A** | Audio |
| **Shift+P** | Physical |
| **Shift+H** | Photo |
| **Shift+W** | Web |
| **Shift+B** | Business |
| **Shift+G** | Global |

### Quick Actions (when category is selected)

| Keys | Action |
|------|--------|
| **Ctrl+N** | Create new project |
| **G** or **0** | Open category folder in Explorer |
| **N** or **.** | Open category notes file |
| **/** | Focus search in project tracker |

---

## Panel Navigation Order

```
Left Panel (W/S cycle):        Right Panel:
┌─────────────────────┐       ┌─────────────────────┐
│ [1] CATEGORIES      │  ←A─  │                     │
│     (2x3 grid)      │  ─D→  │  [4] PROJECT        │
├─────────────────────┤       │      TRACKER        │
│ [2] OPERATIONS      │       │                     │
│     (2x1 grid)      │       │                     │
├─────────────────────┤       │                     │
│ [3] TOOLS           │       │                     │
│     (vertical list) │       │                     │
└─────────────────────┘       └─────────────────────┘
```

Note: Scope (1/2/3) and Status (4/5/6) filters are **not** in WASD navigation - they're direct access via number keys.

---

## Visual Focus Indicators

- **Blue accent bar**: Appears on the left edge of the focused panel (categories grid, operations grid, or tools section)
- **Darkened background**: Focused tool buttons have a slightly darker background
- **Status bar hints**: Shows available shortcuts when hovering over buttons

---

## Tips

1. **Fast category switching**: Use Shift+Letter to jump directly to any category
2. **Quick folder access**: Select a category, then press G to open its folder
3. **Search shortcut**: Press / or Ctrl+F from anywhere to search projects
4. **Cycle scope quickly**: Use backtick (`) to cycle Personal → Work → All
5. **Keyboard-only workflow**: WASD + Arrows + Enter = full navigation without mouse

---

## Startup Apps

There's no keyboard shortcut for this — it's a settings tab. Open `Ctrl+,` → **Startup Apps**.

- Picker pulls from the same `workstation_apps` registry the **Workstation Apps** tab uses; **Browse for file...** adds anything else (.exe, .lnk, .bat, .url).
- Per app you set: enable toggle, monitor, virtual desktop, position mode (maximize / fullscreen / free), and launch order.
- **Install scheduled task** registers `FastRack_StartupLauncher` (logon-triggered, current-user privilege). **Test now** runs the launcher manually in a visible console.
- **Import legacy Startup folder** pulls in shortcuts from `C:\Users\<you>\Startup\DesktopN\` — useful if you're migrating from the old `1_StartupScript_AppsToDesktop.ps1` workflow. The old script and its scheduled task are not touched; uninstall them yourself once you're happy.
- Config sidecar: `%LOCALAPPDATA%\PipelineManager\startup_apps.json`. Logs: `%LOCALAPPDATA%\PipelineManager\logs\startup_launcher.log`.
