"""
Shared Folder Tree Parser

Parses folder-structure template text and creates folder structures.

Two formats are accepted (auto-detected per file):

Indented format (preferred, easy to hand-edit) -- one folder per line, children
indented under their parent with spaces or tabs. Lines starting with "#" and
blank lines are ignored:

    # My template
    00_Incoming
    01_Production
        Shot
            Shot_01_010
    _LIBRARY

Windows `tree /a /f` ASCII format (still supported):
    +---FolderA
    |   +---SubFolder
    |   \\---AnotherSub
    \\---FolderB

Annotations:
    +---ShotFolder [CONDITIONAL:shots]    — skipped when conditionals['shots'] is False

Placeholder replacement:
    Folder names containing keys from `replacements` dict get substituted.
    e.g. "YYY-MM-DD" replaced with actual date when replacements={"YYY-MM-DD": "2025-01-15"}
"""

import os
import re


_TREE_LINE = re.compile(r'^((?:[| ] {3})*)[+\\]---(.+)$')
_COND = re.compile(r'^(.+?)\s+\[CONDITIONAL:(\w+)\]$')


def _parse_indented(lines):
    """Parse the indentation-based format (see module docstring)."""
    result = []
    stack = []  # (indent_width, folder_name)
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())
        name_part = line.strip()
        cond = _COND.match(name_part)
        name, conditional = (cond.group(1).strip(), cond.group(2)) if cond else (name_part, None)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, name))
        result.append(('/'.join(n for _, n in stack), conditional))
    return result


def parse_tree(text):
    """Parse tree /a /f formatted text into a list of relative folder paths.

    Args:
        text: String containing tree-formatted folder structure.

    Returns:
        List of (relative_path, conditional_tag_or_None) tuples.
    """
    lines = text.strip().splitlines()
    if not any(_TREE_LINE.match(l.rstrip()) for l in lines):
        return _parse_indented(lines)
    result = []
    # Stack of (indent_level, folder_name) for tracking hierarchy
    stack = []

    for line in lines:
        # Skip empty lines and lines that are just the tree connector
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith('#'):
            continue

        # Match tree branch lines: optional prefix of "|   " segments,
        # then "+---" or "\---" followed by folder name
        match = re.match(r'^((?:[| ] {3})*)[+\\]---(.+)$', stripped)
        if not match:
            continue

        prefix = match.group(1)
        name_part = match.group(2).strip()

        # Calculate depth from prefix (each "|   " or "    " is one level)
        depth = len(prefix) // 4

        # Extract conditional tag if present
        cond_match = re.match(r'^(.+?)\s+\[CONDITIONAL:(\w+)\]$', name_part)
        if cond_match:
            folder_name = cond_match.group(1).strip()
            conditional = cond_match.group(2)
        else:
            folder_name = name_part
            conditional = None

        # Trim stack to current depth
        stack = stack[:depth]
        stack.append(folder_name)

        path = '/'.join(stack)
        result.append((path, conditional))

    return result


def parse_tree_file(filepath):
    """Parse a tree definition file.

    Args:
        filepath: Path to .txt file containing tree structure.

    Returns:
        List of (relative_path, conditional_tag_or_None) tuples.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        return parse_tree(f.read())


def create_structure(base_path, tree, replacements=None, conditionals=None):
    """Create folder structure from parsed tree data.

    Args:
        base_path: Root directory to create folders in.
        tree: List of (relative_path, conditional_tag) tuples from parse_tree().
        replacements: Dict of {placeholder: value} for folder name substitution.
        conditionals: Dict of {tag: bool}. Folders tagged with [CONDITIONAL:tag]
                      are skipped when conditionals[tag] is False.

    Returns:
        List of created folder paths (absolute).
    """
    if replacements is None:
        replacements = {}
    if conditionals is None:
        conditionals = {}

    created = []
    # Track which conditional paths were skipped so children are also skipped
    skipped_prefixes = []

    for path, conditional in tree:
        # Check if this path is a child of a skipped conditional folder
        skip = False
        for prefix in skipped_prefixes:
            if path.startswith(prefix + '/'):
                skip = True
                break

        if skip:
            continue

        # Check conditional
        if conditional and not conditionals.get(conditional, True):
            skipped_prefixes.append(path)
            continue

        # Apply replacements to each path segment
        parts = path.split('/')
        replaced_parts = []
        for part in parts:
            for placeholder, value in replacements.items():
                part = part.replace(placeholder, value)
            replaced_parts.append(part)

        final_path = os.path.join(base_path, *replaced_parts)
        os.makedirs(final_path, exist_ok=True)
        created.append(final_path)

    return created


def create_gitkeep_files(base_path, created_paths):
    """Create .gitkeep files in leaf directories (dirs with no subdirectories).

    Args:
        base_path: Root directory of the structure.
        created_paths: List of created folder paths from create_structure().
    """
    # A folder is a leaf if no other created path has it as a prefix
    created_set = set(created_paths)
    for path in created_paths:
        is_leaf = True
        for other in created_set:
            if other != path and other.startswith(path + os.sep):
                is_leaf = False
                break
        if is_leaf:
            gitkeep = os.path.join(path, '.gitkeep')
            if not os.path.exists(gitkeep):
                open(gitkeep, 'a').close()
