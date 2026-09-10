# Tests Directory

This directory contains test files for FastRack.

## Running Tests

### Run all tests
```bash
python -m pytest tests/
```

### Run specific test file
```bash
python -m pytest tests/test_specific.py
```

### Run with coverage
```bash
python -m pytest --cov=modules tests/
```

## Test Structure

```
tests/
├── README.md              # This file
├── test_pipeline.py       # Tests for main pipeline
├── test_audio_scripts.py  # Tests for audio scripts
├── test_visual_scripts.py # Tests for visual scripts
└── fixtures/              # Test fixtures and sample data
```

## Writing Tests

Use the unittest or pytest framework:

```python
import unittest
from modules.PipelineScript_Example import some_function

class TestExample(unittest.TestCase):
    def test_basic_functionality(self):
        result = some_function("test")
        self.assertEqual(result, expected_value)

if __name__ == '__main__':
    unittest.main()
```

## Test Coverage

Aim for:
- Core functionality: 80%+ coverage
- Pipeline scripts: 60%+ coverage
- UI components: Manual testing + basic unit tests

## Notes

- Tests should be independent and repeatable
- Use fixtures for test data
- Mock external dependencies (file system, network, etc.)
- Test both success and failure cases
