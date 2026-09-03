from __future__ import annotations

from pathlib import Path
from typing import Any

from .manifest import write_yaml


def write_featured(lazymind_root: Path, skills: list[dict[str, Any]], dry_run: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in skills:
        if not item.get("onboard_as", {}).get("featured"):
            continue
        featured = item["featured"]
        featured_id = featured["id"]
        target_dir = lazymind_root / "skills" / "featured" / featured_id
        definition = build_definition(item)
        if not dry_run:
            (target_dir / "assets").mkdir(parents=True, exist_ok=True)
            write_yaml(target_dir / "featured.yaml", definition)
        results.append({"id": featured_id, "path": str(target_dir / "featured.yaml"), "dry_run": dry_run})
    return results


def build_definition(item: dict[str, Any]) -> dict[str, Any]:
    featured = item["featured"]
    builtin = item.get("builtin") or {}
    assets = {
        "cover": {"file": "assets/cover.png", "role": "cover"},
    }
    tasks = []
    for prompt in featured.get("demo_prompts", []):
        task_id = prompt.get("id", "core-demo")
        html_asset = f"{task_id.replace('-', '_')}_output"
        assets[html_asset] = {"file": f"assets/{task_id}.html", "role": "result"}
        tasks.append(
            {
                "id": task_id,
                "selector": {
                    "title": prompt.get("title", featured["title"]),
                    "description": prompt.get("description", featured["description"]),
                    "output_label": featured["output_label"],
                },
                "launch": {
                    "prompt_short": prompt.get("prompt_short", prompt["prompt"][:80]),
                    "prompt": prompt["prompt"],
                },
                "replay": {
                    "steps": [
                        {"title": "读取 Skill", "description": "解析 Skill 指令与必要引用"},
                        {"title": "执行核心流程", "description": "按示例任务完成主要工作"},
                        {"title": "生成结果", "description": "输出可复现的展示结果"},
                    ]
                },
                "result": {
                    "template": "html_preview_v1",
                    "eyebrow": featured["output_label"],
                    "title": prompt.get("title", featured["title"]),
                    "summary": featured["description"],
                    "html_asset": html_asset,
                },
            }
        )
    return {
        "schema_version": 2,
        "id": featured["id"],
        "type": featured["type"],
        "version": item["version"],
        "status": featured["status"],
        "default_locale": featured["default_locale"],
        "provider": item["provider"],
        "skill": {
            "source_url": builtin.get("path") or item["link"],
            "required_version": item["version"],
        },
        "placement": {
            "home": bool(featured.get("home")),
            "gallery": bool(featured.get("gallery")),
            "order": int(featured.get("order") or 100),
        },
        "classification": {
            "category": featured["category"],
            "tags": featured.get("tags") or [featured["category"]],
        },
        "assets": assets,
        "presentation": {
            "card": {
                "title": featured["title"],
                "description": featured["description"],
                "output_type": featured["output_type"],
                "output_label": featured["output_label"],
                "cover_asset": "cover",
                "result_summary": featured["description"],
            },
            "detail": {
                "title": featured["title"],
                "description": featured["description"],
                "attachment_hint": featured.get("attachment_hint", "可选输入材料"),
            },
        },
        "tasks": tasks,
    }
