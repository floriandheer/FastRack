"""
Sandbox Tag Store
Author: Florian Dheer
Description: Lightweight JSON-backed tag store for the Sandbox file browser.
Maps normalized file/folder paths under the Sandbox root to a list of
free-form user tags. Mirrors shared_project_db.py's whole-file JSON pattern
(personal-scale data, no concurrency control needed).
"""

import json
import os
import shutil
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from shared_logging import get_logger
from shared_appdata import get_appdata_path

logger = get_logger(__name__)


class SandboxTagStore:
    """Manages free-form tags on Sandbox files/folders in JSON format.

    Schema:
    {
        "version": "1.0.0",
        "tags": {"<normalized path>": ["tag1", "tag2"], ...}
    }
    """

    def __init__(self, store_path: Optional[str] = None):
        if store_path is None:
            app_data = get_appdata_path()
            app_data.mkdir(parents=True, exist_ok=True)
            self.store_path = app_data / "sandbox_tags.json"
        else:
            self.store_path = Path(store_path)

        self.data = self._load_or_create()

    def _create_empty(self) -> Dict:
        return {"version": "1.0.0", "tags": {}}

    def _backup_corrupt_store(self):
        if self.store_path.exists():
            backup_path = self.store_path.with_suffix('.json.bak')
            shutil.copy2(self.store_path, backup_path)
            logger.warning(f"Backed up corrupt tag store to: {backup_path}")

    def _load_or_create(self) -> Dict:
        try:
            if self.store_path.exists():
                with open(self.store_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and isinstance(data.get("tags"), dict):
                        return data
                    logger.error("Invalid sandbox tag store schema, creating new store")
                    self._backup_corrupt_store()
                    return self._create_empty()
            return self._create_empty()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse sandbox tag store JSON: {e}")
            self._backup_corrupt_store()
            return self._create_empty()
        except Exception as e:
            logger.error(f"Error loading sandbox tag store: {e}")
            return self._create_empty()

    def _save(self):
        try:
            with open(self.store_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save sandbox tag store: {e}")
            raise

    @staticmethod
    def normalize(path: str) -> str:
        """Normalize a path for use as a dict key (case/slash-insensitive)."""
        return os.path.normcase(os.path.abspath(path))

    def get_tags(self, path: str) -> List[str]:
        return list(self.data["tags"].get(self.normalize(path), []))

    def set_tags(self, path: str, tags: List[str]) -> None:
        """Replace the tag list for a path. Empty list removes the entry."""
        key = self.normalize(path)
        cleaned = sorted({t.strip() for t in tags if t.strip()})
        if cleaned:
            self.data["tags"][key] = cleaned
        else:
            self.data["tags"].pop(key, None)
        self._save()

    def all_tags(self) -> Dict[str, int]:
        """tag -> number of items carrying it, most-used first isn't sorted
        here; callers sort as needed."""
        counts: Dict[str, int] = {}
        for tags in self.data["tags"].values():
            for tag in tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    def paths_with_tag(self, tag: str) -> List[str]:
        return [path for path, tags in self.data["tags"].items() if tag in tags]
