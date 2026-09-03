from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .manifest import load_yaml, write_yaml


def update_builtin_sources(lazymind_root: Path, skills: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    path = lazymind_root / "skills" / "builtin-sources.yaml"
    data = load_yaml(path) if path.exists() else {"schema_version": 1, "bundled_skills": [], "skills": []}
    bundled = ensure_list(data, "bundled_skills")
    remote = ensure_list(data, "skills")
    existing = {entry.get("uid"): entry for entry in bundled if isinstance(entry, dict)}
    existing_remote = {entry.get("source_url"): entry for entry in remote if isinstance(entry, dict)}
    changes: list[str] = []
    for item in skills:
        if not item.get("onboard_as", {}).get("builtin"):
            continue
        builtin = item["builtin"]
        if is_remote_link(item["link"]):
            remove_stale_bundled_entry(bundled, builtin)
            entry = {
                "source_url": item["link"],
                "category": builtin["category"],
                "provider": builtin.get("provider") or item.get("provider"),
            }
            if entry["source_url"] in existing_remote:
                existing_remote[entry["source_url"]].update(entry)
                changes.append(f"update remote builtin {entry['source_url']}")
            else:
                remote.append(entry)
                existing_remote[entry["source_url"]] = entry
                changes.append(f"add remote builtin {entry['source_url']}")
        else:
            entry = {
                "uid": builtin["uid"],
                "path": builtin["path"],
                "category": builtin["category"],
                "version": str(builtin["version"]),
                "provider": builtin.get("provider") or item.get("provider"),
            }
            if entry["uid"] in existing:
                existing[entry["uid"]].update(entry)
                changes.append(f"update bundled builtin {entry['uid']}")
            else:
                bundled.append(entry)
                existing[entry["uid"]] = entry
                changes.append(f"add bundled builtin {entry['uid']}")
    if not dry_run:
        write_yaml(path, data)
    return {"path": str(path), "changes": changes, "dry_run": dry_run}


def is_remote_link(link: str) -> bool:
    parsed = urlparse(link)
    return parsed.scheme in {"http", "https"}


def ensure_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if isinstance(value, list):
        return value
    normalized: list[Any] = []
    data[key] = normalized
    return normalized


def remove_stale_bundled_entry(bundled: list[Any], builtin: dict[str, Any]) -> None:
    uid = builtin.get("uid")
    path = builtin.get("path")
    bundled[:] = [
        entry
        for entry in bundled
        if not (
            isinstance(entry, dict)
            and ((uid and entry.get("uid") == uid) or (path and entry.get("path") == path))
        )
    ]
