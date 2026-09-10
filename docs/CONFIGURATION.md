# Configuration Guide

## Overview

FastRack can be customized by editing configuration values in the main script or by creating custom configuration files.

## Basic Configuration

### Customizing Base Paths

Edit `fastrack_hub.py` to change the base folder paths for each category:

```python
CREATIVE_CATEGORIES = {
    "AUDIO": {
        "name": "Audio",
        "folder_path": "I:\\Audio",  # ← Change this
        # ...
    },
    "PHOTO": {
        "folder_path": "I:\\Photo",  # ← Change this
        # ...
    },
    # ... etc
}
```

### Customizing Colors

The application uses a professional dark theme. To customize colors, edit the `COLORS` dictionary:

```python
COLORS = {
    "bg_primary": "#0d1117",      # Main background
    "bg_secondary": "#161b22",    # Secondary background
    "accent": "#58a6ff",          # Accent color
    # ... etc
}
```

### Category Colors

Customize the color coding for each category:

```python
CATEGORY_COLORS = {
    "AUDIO": "#9333ea",      # Purple
    "PHOTO": "#10b981",      # Emerald
    "VISUAL": "#f97316",     # Orange
    # ... etc
}
```

## Advanced Configuration

### Adding Custom Scripts

1. **Create your script** in the `modules/` directory
   - Follow naming: `PipelineScript_Category_ScriptName.py`
   - Include proper docstring and description

2. **Register the script** in `fastrack_hub.py`:

```python
"your_script": {
    "name": "Your Script Name",
    "path": os.path.join(SCRIPTS_DIR, "PipelineScript_Category_ScriptName.py"),
    "description": "What your script does",
    "icon": "📝"  # Optional emoji icon
}
```

3. **Place in appropriate category**:
   - Under main category for top-level scripts
   - Under subcategory for specialized scripts

### Creating a Configuration File

For more advanced users, you can create a `config/settings.py` file:

```python
# config/settings.py

# Base paths
AUDIO_BASE_PATH = "I:\\Audio"
PHOTO_BASE_PATH = "I:\\Photo"
VISUAL_BASE_PATH = "I:\\Visual"
WEB_BASE_PATH = "I:\\Web"
BUSINESS_BASE_PATH = "I:\\Business"

# UI Settings
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800
THEME = "dark"

# Logging
LOG_LEVEL = "INFO"
LOG_FILE = "logs/pipeline.log"

# Script execution
DEFAULT_TIMEOUT = 300  # seconds
SHOW_CONSOLE_OUTPUT = True
```

Then import in main script:
```python
try:
    from config.settings import *
except ImportError:
    # Use defaults
    pass
```

## Environment Variables

You can also use environment variables for sensitive or machine-specific settings:

```bash
# .env file
PIPELINE_AUDIO_PATH=/mnt/audio
PIPELINE_PHOTO_PATH=/mnt/photo
PIPELINE_LOG_LEVEL=DEBUG
```

Load with python-dotenv:
```python
from dotenv import load_dotenv
load_dotenv()

AUDIO_PATH = os.getenv('PIPELINE_AUDIO_PATH', 'I:\\Audio')
```

## Script-Specific Configuration

### MusicBee Backup Script

Edit variables in `PipelineScript_Audio_MusicBeeBackup.py`:
```python
MUSICBEE_PATH = "C:\\Program Files (x86)\\MusicBee"
ONEDRIVE_PATH = "C:\\Users\\YourName\\OneDrive\\MusicBee"
```

### Invoice Checker

The Invoice Checker uses invoice2data with custom YAML templates for automatic invoice renaming. Templates are stored in `templates/invoice_templates/`. To add a new vendor, create a `.yml` file with `issuer`, `keywords`, and `fields.date` entries.

## Logging Configuration

Configure logging in the main script:

```python
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for verbose output
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/pipeline.log'),
        logging.StreamHandler()
    ]
)
```

## Best Practices

1. **Never commit sensitive data** - Use .env files for secrets
2. **Use config files** - Keep configuration separate from code
3. **Document changes** - Comment why you changed defaults
4. **Version control** - Track config changes in CHANGELOG.md
5. **Test configurations** - Verify paths exist before running scripts

## See Also

- [README.md](../README.md) - Main documentation
- [INSTALLATION.md](INSTALLATION.md) - Installation guide
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
