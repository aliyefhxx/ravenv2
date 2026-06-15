"""Persistent storage əvəzinə yüngül runtime state manager."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class CloneSnapshot:
    user_id: int
    original_first: str
    original_last: str
    original_bio: str
    original_photo: bytes


_settings: dict[str, str] = {}
_welcomes: dict[int, str] = {}
_clones: dict[int, CloneSnapshot] = {}
_blocks: set[int] = set()


async def init_db():
    return True


def pool():
    raise RuntimeError("Persistent DB bu build-də deaktiv edilib")


async def set_setting(key: str, value: str):
    _settings[key] = value


async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    return _settings.get(key, default)


async def save_welcome(chat_id: int, message: str):
    _welcomes[chat_id] = message


async def get_welcome(chat_id: int) -> Optional[str]:
    return _welcomes.get(chat_id)


async def save_clone(
    user_id: int,
    original_first: str,
    original_last: str,
    original_bio: str,
    original_photo: bytes,
):
    _clones[user_id] = CloneSnapshot(
        user_id=user_id,
        original_first=original_first,
        original_last=original_last,
        original_bio=original_bio,
        original_photo=original_photo,
    )


async def get_clone(user_id: int) -> Optional[CloneSnapshot]:
    return _clones.get(user_id)


async def delete_clone(user_id: int):
    _clones.pop(user_id, None)


async def add_block(user_id: int):
    _blocks.add(user_id)


async def remove_block(user_id: int):
    _blocks.discard(user_id)


async def is_blocked(user_id: int) -> bool:
    return user_id in _blocks
