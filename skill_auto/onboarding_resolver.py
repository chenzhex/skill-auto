from __future__ import annotations

from pathlib import Path
from typing import Any

from .downloader import safe_name, stable_id
from .manifest import load_yaml, require_skill_list, write_yaml


def resolve_onboarding_manifest(manifest_path: Path, out_path: Path | None = None) -> dict[str, Any]:
    data = load_yaml(manifest_path)
    skills = require_skill_list(data, manifest_path)
    resolved: list[dict[str, Any]] = []
    for item in skills:
        onboard_as = item.get("onboard_as") or {}
        if not isinstance(onboard_as, dict):
            raise ValueError(f"{item['name']} onboard_as must be a mapping")
        builtin_enabled = bool(onboard_as.get("builtin"))
        featured_enabled = bool(onboard_as.get("featured"))
        if not builtin_enabled and not featured_enabled:
            raise ValueError(f"{item['name']} must enable builtin or featured onboarding")

        name = safe_name(item["name"])
        provider = item.get("provider") or infer_provider(item["link"])
        version = str(item.get("version") or "1.0.0")
        category = item.get("category") or infer_category(item)
        full = dict(item)
        full["provider"] = provider
        full["version"] = version
        full["category"] = category
        if builtin_enabled:
            builtin = dict(item.get("builtin") or {})
            builtin.setdefault("uid", "bsk_" + stable_id(item["name"], item["link"]).upper())
            builtin.setdefault("path", f"{category}/{name}")
            builtin.setdefault("category", category)
            builtin.setdefault("provider", provider)
            builtin.setdefault("version", version)
            builtin.setdefault("market_visible", True)
            full["builtin"] = builtin
        if featured_enabled:
            featured = dict(item.get("featured") or {})
            featured.setdefault("id", name)
            featured.setdefault("type", "work")
            featured.setdefault("status", "published")
            featured.setdefault("default_locale", "zh-CN")
            featured.setdefault("category", item.get("featured_category") or category_label(category))
            featured.setdefault("tags", item.get("tags") or [category_label(category)])
            featured.setdefault("home", False)
            featured.setdefault("gallery", True)
            featured.setdefault("title", item.get("title") or item["name"])
            featured.setdefault("description", item.get("description") or f"{item['name']} Skill 示例能力")
            featured.setdefault("output_type", item.get("output_type") or "report")
            featured.setdefault("output_label", item.get("output_label") or "结果")
            featured.setdefault("demo_prompts", item.get("demo_prompts") or default_demo_prompts(item["name"]))
            full["featured"] = featured
        resolved.append(full)

    output = {**data, "skills": resolved}
    if out_path:
        write_yaml(out_path, output)
    return output


def infer_provider(link: str) -> str:
    return "GitHub" if "github.com" in link else "LazyMind"


def infer_category(item: dict[str, Any]) -> str:
    text = " ".join(str(item.get(key, "")) for key in ("name", "description", "link")).lower()
    if any(word in text for word in ("paper", "search", "research")):
        return "search"
    if any(word in text for word in ("poster", "image", "design")):
        return "design"
    if any(word in text for word in ("chat", "reply")):
        return "chat"
    if any(word in text for word in ("write", "novel", "copy")):
        return "writing"
    return "work"


def category_label(category: str) -> str:
    return {
        "search": "研究检索",
        "design": "设计创作",
        "chat": "对话回复",
        "writing": "内容写作",
        "work": "通用工作",
    }.get(category, category)


def default_demo_prompts(name: str) -> list[dict[str, str]]:
    return [
        {
            "id": "core-demo",
            "title": f"{name} 核心示例",
            "prompt": f"请使用 {name} Skill 完成一个适合展示的核心示例任务，并输出结构化结果。",
        }
    ]
