# Contributing Guidelines

## Overview

While this is a personal pipeline management system, improvements and suggestions are welcome!

## Getting Started

1. **Understand the codebase**
   - Review [README.md](../README.md)
   - Check [CONFIGURATION.md](CONFIGURATION.md)
   - Explore existing scripts in `modules/`

2. **Set up your environment**
   - Install dependencies: `python install_dependencies.py`
   - Create a virtual environment (recommended)
   - Test the application works correctly

## Code Style

### Python Style Guide

- Follow [PEP 8](https://pep8.org/)
- Use 4 spaces for indentation (no tabs)
- Maximum line length: 100 characters
- Use descriptive variable names

### Naming Conventions

- **Files**: `PipelineScript_Category_ScriptName.py`
- **Classes**: `PascalCase`
- **Functions**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`

### Example Code

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module description here.

Author: Your Name
Date: YYYY-MM-DD
"""

import os
import sys
from pathlib import Path

# Constants
DEFAULT_PATH = "I:\\Projects"
MAX_RETRIES = 3

def create_project_structure(project_name: str, base_path: str = DEFAULT_PATH) -> bool:
    """
    Create a standardized project folder structure.

    Args:
        project_name: Name of the project
        base_path: Base directory for projects

    Returns:
        True if successful, False otherwise
    """
    try:
        project_path = Path(base_path) / project_name
        project_path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False
```

## Adding New Scripts

### 1. Create the Script

Create a new file in `modules/` following the naming convention:

```
modules/PipelineScript_Category_YourScript.py
```

### 2. Script Template

Use this template for consistency:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Script: [Category] - [Script Name]

Description: Brief description of what this script does

Author: Your Name
Date: YYYY-MM-DD
Version: 1.0.0
"""

import os
import sys
from pathlib import Path

def main():
    """Main execution function."""
    print("Starting [Script Name]...")

    # Your code here

    print("Script completed successfully!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScript cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
```

### 3. Register the Script

Add your script to `fastrack_hub.py`:

```python
"your_script": {
    "name": "Your Script Name",
    "path": os.path.join(SCRIPTS_DIR, "PipelineScript_Category_YourScript.py"),
    "description": "Clear description of what it does",
    "icon": "📝"
}
```

### 4. Update Dependencies

If your script requires new packages:

1. Add to `requirements.txt`
2. Update `install_dependencies.py` package lists
3. Document in README.md

## Testing

### Manual Testing

1. **Test the script standalone**
   ```bash
   python modules/PipelineScript_Category_YourScript.py
   ```

2. **Test through the GUI**
   - Launch the main application
   - Navigate to your script
   - Execute and verify output

3. **Test error handling**
   - Try invalid inputs
   - Test with missing paths
   - Verify error messages are helpful

### Creating Tests

Create tests in `tests/` directory:

```python
# tests/test_your_script.py
import unittest
from modules.PipelineScript_Category_YourScript import main

class TestYourScript(unittest.TestCase):
    def test_basic_functionality(self):
        result = main()
        self.assertTrue(result)

if __name__ == '__main__':
    unittest.main()
```

## Documentation

### Code Documentation

- Add docstrings to all functions and classes
- Include type hints where appropriate
- Comment complex logic

### User Documentation

Update relevant files:
- `README.md` - Add script to available scripts list
- `CHANGELOG.md` - Document new features
- Create specific docs in `docs/` if needed

## Submitting Changes

### Before Submitting

- [ ] Code follows PEP 8 style guide
- [ ] Script is tested and working
- [ ] Dependencies are documented
- [ ] README.md is updated
- [ ] CHANGELOG.md is updated
- [ ] No sensitive data in code

### Change Description

When submitting changes, include:

1. **What changed** - Clear description
2. **Why it changed** - Rationale for the change
3. **Testing done** - How you verified it works
4. **Dependencies** - Any new requirements

## Questions or Issues?

- Check existing documentation first
- Review similar scripts for examples
- Refer to inline comments in codebase

## License

By contributing, you agree that your contributions will be subject to the project's license.
