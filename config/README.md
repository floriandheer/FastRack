# Configuration Directory

This directory contains configuration files for FastRack.

## Usage

### Option 1: Python Configuration

Create a `settings.py` file in this directory:

```python
# config/settings.py

# Base paths for each category
AUDIO_BASE_PATH = "I:\\Audio"
PHOTO_BASE_PATH = "I:\\Photo"
VISUAL_BASE_PATH = "I:\\Visual"
WEB_BASE_PATH = "I:\\Web"
BUSINESS_BASE_PATH = "I:\\Business"

# UI Settings
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800

# Logging
LOG_LEVEL = "INFO"
LOG_DIR = "logs"
```

### Option 2: Environment Variables

Create a `.env` file in the project root:

```bash
# .env
PIPELINE_AUDIO_PATH=/path/to/audio
PIPELINE_PHOTO_PATH=/path/to/photo
PIPELINE_LOG_LEVEL=DEBUG
```

## Files

- `README.md` - This file
- `settings.example.py` - Example configuration file (copy to settings.py)

## Notes

- Configuration files in this directory are ignored by git (see .gitignore)
- Never commit sensitive information or absolute paths to version control
- Use `settings.example.py` as a template
