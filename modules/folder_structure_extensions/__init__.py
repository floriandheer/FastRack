"""
Folder structure creator extensions.

The base class defines hook methods that the GenericFolderStructureCreator calls
when a manifest entry has a non-None ``extension`` field. Default implementations
are no-ops or fall through to the generic behavior, so subclasses only override
what they actually need to change.
"""

from typing import Optional, List, Dict, Any
import tkinter as tk


class FolderStructureExtension:
    """Base class for per-subtype customization of the generic folder creator.

    The extension is instantiated with a reference to the creator and may read
    its widget state directly (e.g. ``self.creator.client_name_var.get()``).
    """

    def __init__(self, creator):
        self.creator = creator

    # ---- Form construction hooks ----

    def build_main_inputs(self, parent_frame) -> Optional[List[tk.Widget]]:
        """Replace the standard Client/Project row.

        Return a list of focusable widgets if this extension renders its own
        primary inputs (e.g. Photo's date/location/activity). Return None to
        use the standard Client + Project row.
        """
        return None

    def build_extra_widgets(self, parent_frame) -> List[tk.Widget]:
        """Add widgets to the secondary row beyond the manifest's extra_fields.

        Return any focusable widgets to be added to the keyboard nav order.
        """
        return []

    def setup_extra_keyboard_shortcuts(self) -> None:
        """Bind additional global keyboard shortcuts (e.g. O, J for Physical)."""
        pass

    # ---- Behavior hooks ----

    def build_folder_name(self) -> Optional[str]:
        """Override the folder-name builder.

        Return None to use the standard ``{date}_{prefix}_{client}_{project}``
        construction.
        """
        return None

    def get_target_directory(self, base_dir: str) -> Optional[str]:
        """Override target-directory routing (e.g. _Personal/_Sandbox subfolders).

        Return None to use the standard routing based on supports_personal /
        supports_sandbox checkboxes.
        """
        return None

    def get_replacements(self) -> Dict[str, str]:
        """Tree-template placeholder replacements beyond the default YYY-MM-DD."""
        return {}

    def get_conditionals(self) -> Dict[str, bool]:
        """Tree-template conditional flags."""
        return {}

    def validate_inputs(self) -> Optional[str]:
        """Return an error message string, or None if inputs are valid."""
        return None

    def build_metadata(self) -> Dict[str, Any]:
        """Extra fields merged into project_data['metadata']."""
        return {}

    def build_project_data_overrides(self) -> Dict[str, Any]:
        """Top-level overrides for project_data (e.g. a different client_name rule)."""
        return {}

    def after_structure_created(self, project_dir: str) -> None:
        """Called once the folders exist, before the project is registered."""
        pass

    def write_specs_extras(self, content_parts: List[str]) -> None:
        """Append extra sections to the Project_Details.txt content list."""
        pass
