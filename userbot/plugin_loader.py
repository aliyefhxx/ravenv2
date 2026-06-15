"""GitHub-dan runtime plugin yükləyici."""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import traceback
import types
from dataclasses import dataclass
from typing import Any

import httpx
from telethon import events

from config import Config
from security import analyze_plugin

log = logging.getLogger("plugins")


@dataclass(slots=True)
class PluginRecord:
    name: str
    sha: str
    source_url: str
    module: types.ModuleType
    handlers: list[tuple[Any, Any]]


loaded: dict[str, types.ModuleType] = {}
_loaded_records: dict[str, PluginRecord] = {}
_sync_lock = asyncio.Lock()
_sync_task: asyncio.Task | None = None


class PluginLoaderError(RuntimeError):
    pass


def preprocess_code(code: str) -> str:
    code = re.sub(r"from userbot\..* import .*\n", "", code)
    code = re.sub(r"from userbot import .*\n", "", code)
    code = re.sub(r"Help\s*=\s*CmdHelp\(.*\)", "", code)
    code = re.sub(r"Help\..*", "", code)
    code = re.sub(r"@register\(.*pattern=(.*)\)", r"@client.on(events.NewMessage(pattern=\1))", code)
    return code.replace("brend", "event")


def extract_commands(code: str) -> str:
    patterns = [
        r'pattern=r"\^\\\.([\w]+)"',
        r'pattern="\^\.([\w]+)"',
        r'pattern=r"\.([\w]+)"',
        r'pattern="\.([\w]+)"',
    ]
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(re.findall(pattern, code))
    unique_matches = sorted(set(matches))
    if not unique_matches:
        return "<i>Komanda tapılmadı</i>"
    return ", ".join(f"<code>.{cmd}</code>" for cmd in unique_matches)


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "raven-userbot-plugin-loader",
    }
    if Config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {Config.GITHUB_TOKEN}"
    return headers


async def _fetch_catalog() -> list[dict[str, str]]:
    repo = Config.PLUGIN_SOURCE_REPO
    if not repo:
        log.warning("PLUGIN_SOURCE_REPO tapılmadı, GitHub plugin sync söndürülüb")
        return []

    api_url = (
        f"https://api.github.com/repos/{repo}/contents/"
        f"{Config.PLUGIN_SOURCE_PATH}?ref={Config.PLUGIN_SOURCE_BRANCH}"
    )
    async with httpx.AsyncClient(timeout=20.0, headers=_headers()) as client:
        response = await client.get(api_url)
        response.raise_for_status()
        items = response.json()

    allowlist = {name.lower() for name in Config.PLUGIN_ALLOWLIST}
    plugins: list[dict[str, str]] = []
    for item in items:
        if item.get("type") != "file":
            continue
        name = item.get("name", "")
        if not name.endswith(".py") or name.startswith("_"):
            continue
        stem = name[:-3]
        if allowlist and stem.lower() not in allowlist:
            continue
        plugins.append(
            {
                "name": stem,
                "sha": item.get("sha", ""),
                "download_url": item.get("download_url", ""),
            }
        )
    return plugins


async def _fetch_code(download_url: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, headers=_headers()) as client:
        response = await client.get(download_url)
        response.raise_for_status()
        return response.text


def _snapshot_handlers(client) -> list[tuple[Any, Any]]:
    try:
        return list(client.list_event_handlers())
    except Exception:
        return []


def _diff_handlers(before: list[tuple[Any, Any]], after: list[tuple[Any, Any]]) -> list[tuple[Any, Any]]:
    before_ids = {(id(cb), repr(ev)) for cb, ev in before}
    return [(cb, ev) for cb, ev in after if (id(cb), repr(ev)) not in before_ids]


async def _load_plugin(name: str, sha: str, source_url: str, code: str, client) -> PluginRecord:
    processed = preprocess_code(code)
    safe, reason = analyze_plugin(processed)
    if not safe:
        raise PluginLoaderError(f"Təhlükəsizlik xətası: {reason}")

    module_name = f"plugins.{name}"
    before = _snapshot_handlers(client)
    mod = types.ModuleType(module_name)
    mod.__file__ = source_url
    mod.__package__ = "plugins"
    mod.client = client
    mod.events = events
    mod.__dict__["__builtins__"] = __builtins__
    sys.modules[module_name] = mod

    try:
        exec(compile(processed, source_url, "exec"), mod.__dict__)
        if hasattr(mod, "register"):
            maybe = mod.register(client)
            if asyncio.iscoroutine(maybe):
                await maybe
        after = _snapshot_handlers(client)
        handlers = _diff_handlers(before, after)
        record = PluginRecord(name=name, sha=sha, source_url=source_url, module=mod, handlers=handlers)
        loaded[name] = mod
        _loaded_records[name] = record
        log.info("✅ Plugin yükləndi: %s", name)
        return record
    except Exception as exc:
        sys.modules.pop(module_name, None)
        err = traceback.format_exc()
        log.error("Plugin xətası %s: %s", name, err)
        raise PluginLoaderError(str(exc)) from exc


async def _unload_plugin(name: str, client) -> None:
    record = _loaded_records.pop(name, None)
    loaded.pop(name, None)
    if not record:
        return
    for callback, event_builder in reversed(record.handlers):
        try:
            client.remove_event_handler(callback, event_builder)
        except Exception:
            pass
    sys.modules.pop(record.module.__name__, None)
    log.info("🗑 Plugin unload edildi: %s", name)


async def sync_plugins(client) -> None:
    async with _sync_lock:
        catalog = await _fetch_catalog()
        desired = {item["name"]: item for item in catalog}

        for name in list(_loaded_records):
            if name not in desired:
                await _unload_plugin(name, client)

        for name, item in desired.items():
            current = _loaded_records.get(name)
            if current and current.sha == item["sha"]:
                continue
            if current:
                await _unload_plugin(name, client)
            code = await _fetch_code(item["download_url"])
            try:
                await _load_plugin(name, item["sha"], item["download_url"], code, client)
            except PluginLoaderError as exc:
                log.error("Plugin yüklənmədi %s: %s", name, exc)

        log.info("🔌 Aktiv GitHub plugin sayı: %s", len(_loaded_records))


async def load_all(client):
    await sync_plugins(client)


async def _sync_loop(client):
    while True:
        await asyncio.sleep(Config.PLUGIN_SYNC_INTERVAL)
        try:
            await sync_plugins(client)
        except Exception as exc:
            log.warning("Plugin sync xətası: %s", exc)


async def start_background_sync(client):
    global _sync_task
    if _sync_task and not _sync_task.done():
        return _sync_task
    _sync_task = asyncio.create_task(_sync_loop(client))
    return _sync_task


def stop_background_sync():
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
    _sync_task = None
