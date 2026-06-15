"""Mərkəzi konfiqurasiya"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _detect_repo_from_git() -> str:
    candidates = [
        os.getenv("PLUGIN_SOURCE_REPO", "").strip(),
        os.getenv("GITHUB_REPOSITORY", "").strip(),
        os.getenv("RENDER_GIT_REPOSITORY", "").strip(),
    ]
    for candidate in candidates:
        if candidate:
            return candidate.replace("https://github.com/", "").replace(".git", "").strip("/")

    git_config = Path(__file__).resolve().parents[1] / ".git" / "config"
    if git_config.exists():
        try:
            text = git_config.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("url = ") and "github.com" in line:
                    url = line.split("=", 1)[1].strip()
                    if url.startswith("git@github.com:"):
                        return url.split(":", 1)[1].replace(".git", "").strip("/")
                    if "github.com/" in url:
                        return url.split("github.com/", 1)[1].replace(".git", "").strip("/")
        except Exception:
            pass
    return ""


def _default_plugin_cache_dir() -> str:
    candidates = [
        os.getenv("PLUGIN_CACHE_DIR", "").strip(),
        os.getenv("RENDER_DISK_PATH", "").strip(),
        os.getenv("RENDER_PERSISTENT_DIR", "").strip(),
    ]
    for candidate in candidates:
        if candidate:
            base = Path(candidate).expanduser()
            if base.name == "raven-plugin-cache":
                return str(base)
            return str(base / "raven-plugin-cache")

    render_disk_fallback = Path("/var/data")
    if render_disk_fallback.exists() and render_disk_fallback.is_dir():
        return str(render_disk_fallback / "raven-plugin-cache")

    return str((Path(__file__).resolve().parent / ".plugin_cache").resolve())


class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    SESSION_STRING = os.getenv("SESSION_STRING", "")
    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
    CMD_PREFIX = os.getenv("CMD_PREFIX", ".")
    LOG_TO_SAVED = os.getenv("LOG_TO_SAVED", "1") == "1"

    PLUGIN_SOURCE_REPO = _detect_repo_from_git()
    PLUGIN_SOURCE_BRANCH = os.getenv("PLUGIN_SOURCE_BRANCH", "main")
    PLUGIN_SOURCE_PATH = os.getenv("PLUGIN_SOURCE_PATH", "github_plugins").strip("/")
    PLUGIN_SYNC_INTERVAL = max(60, int(os.getenv("PLUGIN_SYNC_INTERVAL", "300")))
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
    PLUGIN_CACHE_DIR = _default_plugin_cache_dir()
    PLUGIN_AUTO_SYNC = os.getenv("PLUGIN_AUTO_SYNC", "0") == "1"
    PLUGIN_ALLOWLIST = [
        item.strip() for item in os.getenv("PLUGIN_ALLOWLIST", "").split(",") if item.strip()
    ]
