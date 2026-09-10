"""
UI Config Manager - JSON configuration persistence for FastRack.
"""

import os
import json

from shared_logging import get_logger
from ui_pipeline_categories import APP_VERSION, DEFAULT_CONFIG_PATH

logger = get_logger("pipeline")


class ConfigManager:
    """Manages configuration settings for FastRack."""

    def __init__(self, config_path=DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        """Load configuration from file or create default if not exists."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                return self._create_default_config()
        else:
            return self._create_default_config()

    def _create_default_config(self):
        """Create default configuration."""
        config = {
            "version": APP_VERSION,
            "last_main_tab": "creative",
            "last_category": "AUDIO",
            "scripts": {}
        }

        self._save_config(config)
        return config

    def _save_config(self, config=None):
        """Save configuration to file."""
        if config is None:
            config = self.config

        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return False

    def get_script_config(self, category_key, script_key):
        """Get configuration for a specific script."""
        script_id = f"{category_key}_{script_key}"
        if script_id not in self.config.get("scripts", {}):
            self.config.setdefault("scripts", {})[script_id] = {
                "args": [],
                "env_vars": {},
                "last_run": None
            }
            self._save_config()
        return self.config["scripts"][script_id]

    def update_script_config(self, category_key, script_key, new_config):
        """Update configuration for a specific script."""
        script_id = f"{category_key}_{script_key}"
        self.config.setdefault("scripts", {})[script_id] = new_config
        return self._save_config()
