"""Shared YAML frontmatter parse/serialize module for KG node .md files.

Public API:
    parse(file_path) -> (dict, str)
    serialize(frontmatter, body) -> str
    write(file_path, frontmatter, body)
    deep_merge(base, updates) -> dict
"""

import datetime
import json
import os
import tempfile

import yaml


# ---------------------------------------------------------------------------
# Custom YAML Dumper — quotes all strings, preserves key order, short lists
# as flow-style, handles datetime.date objects.
# ---------------------------------------------------------------------------

class _QuotedDumper(yaml.Dumper):
    """YAML Dumper that quotes every string and renders short scalar lists inline."""
    pass


def _str_representer(dumper, data):
    """Always emit strings with double quotes."""
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


def _date_representer(dumper, data):
    """Serialize datetime.date as a quoted YYYY-MM-DD string."""
    return dumper.represent_scalar("tag:yaml.org,2002:str", data.isoformat(), style='"')


def _datetime_representer(dumper, data):
    """Serialize datetime.datetime as a quoted ISO string."""
    return dumper.represent_scalar("tag:yaml.org,2002:str", data.isoformat(), style='"')


def _bool_representer(dumper, data):
    """Emit booleans as lowercase true/false."""
    return dumper.represent_scalar("tag:yaml.org,2002:bool", "true" if data else "false")


def _list_representer(dumper, data):
    """Use flow style for short scalar-only lists, block style otherwise."""
    if len(data) <= 4 and all(isinstance(item, (str, int, float, bool)) for item in data):
        return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=False)


_QuotedDumper.add_representer(str, _str_representer)
_QuotedDumper.add_representer(datetime.date, _date_representer)
_QuotedDumper.add_representer(datetime.datetime, _datetime_representer)
_QuotedDumper.add_representer(bool, _bool_representer)
_QuotedDumper.add_representer(list, _list_representer)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(file_path: str) -> tuple[dict, str]:
    """Parse a node .md file with YAML frontmatter.

    Returns:
        (frontmatter_dict, markdown_body)

    Raises:
        FileNotFoundError: if file does not exist
        ValueError: if file has no valid ``---`` delimited frontmatter
    """
    with open(file_path, "r", encoding="utf-8") as fh:
        content = fh.read()

    if not content.startswith("---"):
        raise ValueError(f"No YAML frontmatter found in {file_path}")

    # Split on the second '---' delimiter
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Incomplete YAML frontmatter in {file_path}")

    yaml_text = parts[1]
    body = parts[2]

    # Strip exactly one leading newline from body (the newline right after ---)
    if body.startswith("\n"):
        body = body[1:]

    frontmatter = yaml.safe_load(yaml_text)
    if frontmatter is None:
        frontmatter = {}

    # Convert any datetime.date values back to strings for consistency
    _normalize_dates(frontmatter)

    return frontmatter, body


def serialize(frontmatter: dict, body: str) -> str:
    """Serialize a frontmatter dict and markdown body into a complete .md file string."""
    yaml_text = yaml.dump(
        frontmatter,
        Dumper=_QuotedDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=120,
    )
    # Ensure body is separated from frontmatter by exactly one newline
    if body and not body.startswith("\n"):
        return f"---\n{yaml_text}---\n\n{body}"
    return f"---\n{yaml_text}---\n{body}"


def write(file_path: str, frontmatter: dict, body: str) -> None:
    """Write frontmatter + body to a .md file atomically.

    Uses write-to-temp-then-rename to prevent corruption on interruption.
    """
    content = serialize(frontmatter, body)
    dir_name = os.path.dirname(os.path.abspath(file_path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".md.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, file_path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def deep_merge(base: dict, updates: dict) -> dict:
    """Recursively merge *updates* into *base* and return the merged dict.

    Merge rules:
    - Scalars: updates overwrite base.
    - Dicts: recursive merge.
    - Lists of dicts with an identifying key: merge by matching key, append
      unmatched items.  Identifying keys are determined by ``_IDENTITY_KEYS``.
    - Scalar lists whose parent key is in ``_SET_UNION_KEYS``: set union.
    - Other lists: updates replace base entirely.
    """
    merged = dict(base)
    for key, update_val in updates.items():
        if key not in merged:
            merged[key] = update_val
            continue

        base_val = merged[key]

        # Both dicts → recurse
        if isinstance(base_val, dict) and isinstance(update_val, dict):
            merged[key] = deep_merge(base_val, update_val)
            continue

        # Both lists
        if isinstance(base_val, list) and isinstance(update_val, list):
            # Set-union for scalar lists in known keys
            if key in _SET_UNION_KEYS:
                seen = set(base_val)
                merged[key] = list(base_val) + [v for v in update_val if v not in seen]
                continue

            # List-of-dicts with an identity key → merge by identity
            id_key = _IDENTITY_KEYS.get(key)
            if id_key and update_val and isinstance(update_val[0], dict):
                merged[key] = _merge_list_of_dicts(base_val, update_val, id_key)
                continue

            # Fallback: replace
            merged[key] = update_val
            continue

        # Default: overwrite
        merged[key] = update_val

    return merged


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Keys that identify a unique item inside a list of dicts.
# For composite keys, the value is a tuple of field names.
_IDENTITY_KEYS: dict[str, str | tuple[str, ...]] = {
    "pubmed_ids": "pmid",
    "external_ids": "id",
    "entities": "normalized_id",
    "cross_kg_links": ("kg", "node"),
}

# Lists that should be merged as set unions (no duplicates).
_SET_UNION_KEYS = {"tags", "related_nodes", "keywords"}


def _merge_list_of_dicts(
    base_list: list[dict],
    update_list: list[dict],
    id_key: str | tuple[str, ...],
) -> list[dict]:
    """Merge two lists of dicts by matching on *id_key*."""
    # Build index of base items
    index: dict[str | tuple, int] = {}
    result = [dict(item) for item in base_list]  # shallow copy each item

    for i, item in enumerate(result):
        k = _extract_key(item, id_key)
        if k is not None:
            index[k] = i

    for update_item in update_list:
        k = _extract_key(update_item, id_key)
        if k is not None and k in index:
            # Merge into existing item
            existing = result[index[k]]
            for field, val in update_item.items():
                existing[field] = val
        else:
            # Append new item
            result.append(dict(update_item))
            if k is not None:
                index[k] = len(result) - 1

    return result


def _extract_key(item: dict, id_key: str | tuple[str, ...]):
    """Extract the identity key value from a dict item."""
    if isinstance(id_key, tuple):
        vals = tuple(item.get(k) for k in id_key)
        return vals if all(v is not None for v in vals) else None
    return item.get(id_key)


def _normalize_dates(obj):
    """Recursively convert datetime.date/datetime values to ISO strings in-place."""
    if isinstance(obj, dict):
        for key in obj:
            if isinstance(obj[key], (datetime.date, datetime.datetime)):
                obj[key] = obj[key].isoformat()
            elif isinstance(obj[key], (dict, list)):
                _normalize_dates(obj[key])
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, (datetime.date, datetime.datetime)):
                obj[i] = item.isoformat()
            elif isinstance(item, (dict, list)):
                _normalize_dates(item)
