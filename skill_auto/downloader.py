from __future__ import annotations

import hashlib
import shutil
import subprocess
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from .models import SkillSource


def source_type(link: str) -> str:
    parsed = urlparse(link)
    if parsed.scheme in {"http", "https"} and "skillhub.cn" in parsed.netloc:
        return "skillhub"
    if parsed.scheme in {"http", "https"} and "github.com" in parsed.netloc:
        return "github"
    if parsed.scheme in {"http", "https"}:
        return "url"
    if Path(link).exists():
        return "local"
    return "unknown"


def stable_id(*parts: str, prefix: str = "") -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}" if prefix else digest


def prepare_source(skill: SkillSource, workspace: Path) -> tuple[Path | None, str | None]:
    """Prepare a Skill source when possible.

    Local paths are copied into the workspace. GitHub URLs are cloned if network
    access and git are available. Failures are intentionally non-fatal so static
    records can still explain what happened.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    kind = source_type(skill.link)
    dest = workspace / safe_name(skill.name)
    if dest.exists():
        shutil.rmtree(dest)
    if kind == "local":
        src = Path(skill.link).expanduser().resolve()
        if src.is_dir():
            shutil.copytree(src, dest)
        elif src.suffix.lower() == ".zip":
            dest.mkdir(parents=True)
            unzip_safe(src, dest)
        else:
            dest.mkdir(parents=True)
            shutil.copy2(src, dest / src.name)
        return dest, None
    if kind == "skillhub":
        try:
            zip_path = workspace / f"{safe_name(skill.name)}.zip"
            download_skillhub(skill.link, zip_path)
            dest.mkdir(parents=True)
            unzip_safe(zip_path, dest)
            return dest, None
        except Exception as exc:  # noqa: BLE001 - converted to trial failure.
            return None, f"fetch_failed: {exc}"
    if kind == "github":
        try:
            repo_url, branch, subdir = parse_github_tree_link(skill.link)
            clone_dest = dest
            if subdir:
                clone_dest = workspace / f"{safe_name(skill.name)}.repo"
                if clone_dest.exists():
                    shutil.rmtree(clone_dest)
            command = ["git", "clone", "--depth", "1"]
            if branch:
                command.extend(["--branch", branch])
            command.extend([repo_url, str(clone_dest)])
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            commit = subprocess.run(
                ["git", "-C", str(clone_dest), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            if subdir:
                source_subdir = clone_dest / subdir
                if not source_subdir.exists() or not source_subdir.is_dir():
                    return None, f"fetch_failed: GitHub subdirectory not found: {subdir}"
                shutil.copytree(source_subdir, dest)
            return dest, commit
        except Exception as exc:  # noqa: BLE001 - converted to trial failure.
            return None, f"fetch_failed: {exc}"
    return None, f"unsupported_source_type: {kind}"


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value).strip("-") or "skill"


def parse_github_tree_link(link: str) -> tuple[str, str | None, Path | None]:
    parsed = urllib.parse.urlparse(link)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) >= 5 and parts[2] == "tree":
        owner, repo, _, branch = parts[:4]
        subdir = Path(*parts[4:])
        return f"https://github.com/{owner}/{repo}.git", branch, subdir
    if len(parts) >= 2:
        owner, repo = parts[:2]
        repo = repo.removesuffix(".git")
        return f"https://github.com/{owner}/{repo}.git", None, None
    return link, None, None


def parse_skillhub_link(link: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(link)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[0] != "skills":
        raise ValueError("SkillHub link must be /skills/{namespace}/{slug}")
    return parts[1], parts[2]


def download_skillhub(link: str, target: Path) -> None:
    url = skillhub_download_url(link)
    request = urllib.request.Request(url, headers={"User-Agent": "skill-auto/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - user-provided test URL.
        if response.status < 200 or response.status >= 300:
            raise ValueError(f"SkillHub download returned HTTP {response.status}")
        target.write_bytes(response.read())


def skillhub_download_url(link: str) -> str:
    namespace, slug = parse_skillhub_link(link)
    query = urllib.parse.urlencode({"slug": slug, "namespace": namespace})
    return f"https://api.skillhub.cn/api/v1/download?{query}"


def unzip_safe(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"unsafe zip path: {member.filename}")
            if member.is_dir():
                continue
            target = dest / member_path
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)
