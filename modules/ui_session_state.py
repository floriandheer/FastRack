"""
Shared session state for category selection and scope filter.

Before this module, the hub (`fastrack_hub.py`) held `selected_categories` +
`current_scopes` and the embedded project tracker
(`fastrack_project_explorer.py`) held its own `selected_categories` +
`filter_scopes`. Either side could drift from the other. `SessionState`
centralises the truth; both sides subscribe via `add_listener` and receive a
notification whenever categories or scopes change.

Stored formats:
- categories: ordered list of **hub category keys** (e.g. "VISUAL", "PHOTO").
  The last element is the "primary" selection. The tracker converts keys to
  display names via the mapper supplied at wiring time.
- scopes: set of strings drawn from ("personal", "client").
"""

from typing import Callable, Iterable, List, Optional, Set

from shared_logging import get_logger

logger = get_logger(__name__)


CHANGE_CATEGORIES = "categories"
CHANGE_SCOPES = "scopes"


class SessionState:
    def __init__(
        self,
        initial_categories: Optional[Iterable[str]] = None,
        initial_scopes: Optional[Iterable[str]] = None,
    ):
        self._categories: List[str] = self._dedup(initial_categories or [])
        self._scopes: Set[str] = {
            s for s in (initial_scopes or {"personal", "client"})
            if s in ("personal", "client")
        }
        self._listeners: List[Callable[[str], None]] = []

    @staticmethod
    def _dedup(items: Iterable[str]) -> List[str]:
        seen = set()
        out = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def add_listener(self, fn: Callable[[str], None]) -> None:
        self._listeners.append(fn)

    def _notify(self, change: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(change)
            except Exception as e:
                logger.error(f"SessionState listener failed: {e}", exc_info=True)

    @property
    def categories(self) -> List[str]:
        return list(self._categories)

    @property
    def primary_category(self) -> Optional[str]:
        return self._categories[-1] if self._categories else None

    @property
    def scopes(self) -> Set[str]:
        return set(self._scopes)

    def set_categories(self, keys: Iterable[str]) -> None:
        new = self._dedup(keys)
        if new != self._categories:
            self._categories = new
            self._notify(CHANGE_CATEGORIES)

    def toggle_category(self, key: str, additive: bool = False) -> None:
        if additive:
            if key in self._categories:
                self._categories.remove(key)
            else:
                self._categories.append(key)
        else:
            self._categories = [key]
        self._notify(CHANGE_CATEGORIES)

    def clear_categories(self) -> None:
        if self._categories:
            self._categories = []
            self._notify(CHANGE_CATEGORIES)

    def set_scopes(self, scopes: Iterable[str]) -> None:
        new = {s for s in scopes if s in ("personal", "client")}
        if new != self._scopes:
            self._scopes = new
            self._notify(CHANGE_SCOPES)

    def toggle_scope(self, scope: str, additive: bool = False) -> None:
        if scope not in ("personal", "client"):
            return
        if additive:
            if scope in self._scopes:
                self._scopes.discard(scope)
            else:
                self._scopes.add(scope)
        else:
            self._scopes = {scope}
        self._notify(CHANGE_SCOPES)
