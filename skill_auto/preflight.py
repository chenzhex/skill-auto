from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


API_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|ACCESS_KEY))\b")
DEPENDENCY_FILES = {
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "package.json": "node",
    "pnpm-lock.yaml": "node",
}
DANGEROUS_PATTERNS = [
    "rm -rf /",
    "sudo ",
    "chmod 777",
    "curl ",
    "wget ",
    "os.system(",
    "subprocess.run(",
]


def inspect_source(path: Path | None, env_values: dict[str, str]) -> dict[str, Any]:
    if path is None:
        return {
            "preflight_status": "fail",
            "security_status": "warn",
            "failure_category": "source_unavailable",
            "failure_technical_reason": "source could not be prepared",
        }

    skill_md = find_skill_md(path)
    required_keys = sorted(detect_env_keys(path))
    missing_keys = [key for key in required_keys if key not in env_values and key not in os.environ]
    dependencies = sorted(detect_dependencies(path))
    security_notes = scan_security_notes(path)
    package_size_mb = round(directory_size(path) / 1024 / 1024, 2)

    if skill_md is None:
        return {
            "preflight_status": "fail",
            "security_status": "warn",
            "failure_category": "invalid_package",
            "failure_user_message": "该 Skill 包中未找到 SKILL.md 或 skill.md。",
            "failure_technical_reason": "missing SKILL.md",
            "suggested_fix": "补充 LazyMind 可识别的 SKILL.md 后再测试或接入。",
            "required_env_keys": required_keys,
            "requires_api_key": bool(required_keys),
            "dependencies": dependencies,
            "package_size_mb": package_size_mb,
        }

    return {
        "preflight_status": "pass" if not missing_keys else "partial",
        "security_status": "warn" if security_notes else "pass",
        "skill_md": str(skill_md),
        "required_env_keys": required_keys,
        "requires_api_key": bool(required_keys),
        "missing_env_keys": missing_keys,
        "dependencies": dependencies,
        "package_size_mb": package_size_mb,
        "runtime_skill_name": detect_skill_name(skill_md),
        "detected_skill_type": detect_skill_type(skill_md),
        "detected_scenario": detect_scenario(skill_md),
        "security_notes": security_notes,
    }


def find_skill_md(path: Path) -> Path | None:
    for candidate in ("SKILL.md", "skill.md"):
        direct = path / candidate
        if direct.exists():
            return direct
    matches = list(path.rglob("SKILL.md")) + list(path.rglob("skill.md"))
    return matches[0] if matches else None


def detect_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for file_path in text_files(path):
        try:
            keys.update(API_KEY_RE.findall(file_path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return keys


def detect_dependencies(path: Path) -> set[str]:
    deps: set[str] = set()
    for filename, label in DEPENDENCY_FILES.items():
        if list(path.rglob(filename)):
            deps.add(label)
    for file_path in text_files(path):
        name = file_path.name.lower()
        if name.endswith((".py", ".md", ".txt", ".toml", ".json")):
            text = file_path.read_text(encoding="utf-8", errors="ignore").lower()
            for binary in ("ffmpeg", "pandoc", "tesseract", "playwright", "chromium"):
                if binary in text:
                    deps.add(binary)
    return deps


def scan_security_notes(path: Path) -> list[str]:
    notes: list[str] = []
    for file_path in text_files(path):
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        for pattern in DANGEROUS_PATTERNS:
            if pattern in text:
                notes.append(f"{file_path.relative_to(path)} contains {pattern!r}")
    return notes[:20]


def detect_skill_type(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8", errors="ignore").lower()
    if any(word in text for word in ("ppt", "presentation", "slide")):
        return "presentation"
    if any(word in text for word in ("image", "poster", "design")):
        return "design"
    if any(word in text for word in ("research", "search", "paper")):
        return "research"
    if any(word in text for word in ("excel", "csv", "data", "chart")):
        return "data"
    if any(word in text for word in ("chat", "reply", "conversation")):
        return "chat"
    return "work"


def detect_skill_name(skill_md: Path) -> str | None:
    text = skill_md.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines()[:80]:
        match = re.match(r"^\s*name\s*:\s*[\"']?([^\"'#]+)", line)
        if match:
            return match.group(1).strip()
    return None


def detect_scenario(skill_md: Path) -> str:
    skill_type = detect_skill_type(skill_md)
    return {
        "presentation": "PPT/演示文稿",
        "design": "设计创作",
        "research": "研究检索",
        "data": "数据分析",
        "chat": "对话回复",
    }.get(skill_type, "通用工作流")


def text_files(path: Path):
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.stat().st_size > 2_000_000:
            continue
        yield file_path


def directory_size(path: Path) -> int:
    total = 0
    for file_path in path.rglob("*"):
        if file_path.is_file():
            total += file_path.stat().st_size
    return total
