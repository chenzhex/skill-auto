from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - exercised in bare Python envs.
    yaml = None


class ManifestError(ValueError):
    """Raised when a manifest is missing required fields."""


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        text = handle.read()
    if yaml is not None:
        data = yaml.safe_load(text) or {}
    else:
        data = parse_simple_yaml(text)
    if not isinstance(data, dict):
        raise ManifestError(f"{path} must contain a YAML mapping")
    return data


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        if yaml is not None:
            yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
        else:
            handle.write(dump_simple_yaml(data))


def require_skill_list(data: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    skills = data.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ManifestError(f"{path} must contain a non-empty skills list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(skills, start=1):
        if not isinstance(item, dict):
            raise ManifestError(f"{path} skills[{index}] must be a mapping")
        name = item.get("name")
        link = item.get("link")
        if not isinstance(name, str) or not name.strip():
            raise ManifestError(f"{path} skills[{index}] is missing name")
        if not isinstance(link, str) or not link.strip():
            raise ManifestError(f"{path} skills[{index}] is missing link")
        normalized.append({**item, "name": name.strip(), "link": link.strip()})
    return normalized


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by skill-auto manifests.

    This is intentionally conservative. It supports mappings, nested mappings,
    lists of mappings, booleans, numbers, nulls, and inline string arrays. Users
    with PyYAML installed get full YAML support.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    last_key_at_indent: dict[int, str] = {}
    raw_lines = text.splitlines()
    for line_index, raw_line in enumerate(raw_lines):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line == "-" or line.startswith("- "):
            item_text = "" if line == "-" else line[2:].strip()
            if not isinstance(parent, list):
                raise ManifestError("list item found outside a list")
            if item_text == "":
                item = {}
                parent.append(item)
                stack.append((indent, item))
                continue
            if ":" in item_text:
                key, value = split_key_value(item_text)
                item: dict[str, Any] = {key: parse_scalar(value)}
                parent.append(item)
                stack.append((indent, item))
                if value == "":
                    child: dict[str, Any] = {}
                    item[key] = child
                    stack.append((indent + 2, child))
            else:
                parent.append(parse_scalar(item_text))
            continue
        key, value = split_key_value(line)
        if not isinstance(parent, dict):
            raise ManifestError("mapping entry found inside a scalar list")
        if value == "":
            next_container: Any = [] if next_content_is_list(raw_lines, line_index, indent) else {}
            parent[key] = next_container
            last_key_at_indent[indent] = key
            stack.append((indent, next_container))
        else:
            parent[key] = parse_scalar(value)
            last_key_at_indent[indent] = key

        # If a following indented mapping appears under an empty key, convert the
        # placeholder list to a dict lazily.
        if isinstance(parent.get(key), list):
            pass
    return root


def next_content_is_list(lines: list[str], current_index: int, current_indent: int) -> bool:
    for next_line in lines[current_index + 1 :]:
        if not next_line.strip() or next_line.lstrip().startswith("#"):
            continue
        next_indent = len(next_line) - len(next_line.lstrip(" "))
        if next_indent <= current_indent:
            return False
        stripped = next_line.strip()
        return stripped == "-" or stripped.startswith("- ")
    return False


def split_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise ManifestError(f"expected key: value, got {line!r}")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        body = value[1:-1].strip()
        if not body:
            return []
        return [parse_scalar(part.strip()) for part in body.split(",")]
    if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def dump_simple_yaml(data: Any, indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and not value:
                lines.append(f"{prefix}{key}: []")
                continue
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(dump_simple_yaml(value, indent + 2).rstrip())
            else:
                lines.append(f"{prefix}{key}: {format_scalar(value)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                lines.append(f"{prefix}-")
                lines.append(dump_simple_yaml(item, indent + 2).rstrip())
            elif isinstance(item, list):
                lines.append(f"{prefix}-")
                lines.append(dump_simple_yaml(item, indent + 2).rstrip())
            else:
                lines.append(f"{prefix}- {format_scalar(item)}")
    else:
        lines.append(f"{prefix}{format_scalar(data)}")
    return "\n".join(line for line in lines if line is not None) + "\n"


def format_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    text = str(value)
    if text == "" or text.strip() != text or any(ch in text for ch in ":#[]{}"):
        return repr(text)
    return text
