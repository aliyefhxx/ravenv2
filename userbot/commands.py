from __future__ import annotations

import asyncio
import io
import logging
import os
import random
import sys
import time
from typing import Iterable

from telethon import Button, events
from telethon.errors import ChatAdminRequiredError, FloodWaitError, UserAdminInvalidError
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
from telethon.tl.functions.photos import DeletePhotosRequest, GetUserPhotosRequest, UploadProfilePhotoRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import ChatBannedRights

from config import Config
import db
import plugin_loader
import ratelimit

log = logging.getLogger("cmds")
P = Config.CMD_PREFIX
START_TIME = time.time()
TAG_CALLBACK_PREFIX = b"tag:"


class TagMode:
    def __init__(self, key: str, title: str, chunk_size: int, max_users: int, delay: float, header: str = ""):
        self.key = key
        self.title = title
        self.chunk_size = chunk_size
        self.max_users = max_users
        self.delay = delay
        self.header = header


TAG_MODES: dict[str, TagMode] = {
    "solo": TagMode("solo", "Tək-tək", 1, 15, 0.6, "🎯 Tək tag"),
    "trio": TagMode("trio", "3-lü", 3, 30, 0.8, "⚡ 3-lü tag"),
    "five": TagMode("five", "5-li", 5, 50, 1.0, "🔥 5-li tag"),
    "wave": TagMode("wave", "Dalğa", 8, 40, 1.2, "🌊 Dalğa tag"),
    "random": TagMode("random", "Random", 4, 20, 0.9, "🎲 Random tag"),
}


def cmd_re(name: str) -> str:
    return rf"(?i)^\{P}{name}(?:\s|$)(.*)"


async def edit_safe(event, text: str, *, buttons=None):
    try:
        await event.edit(text, parse_mode="html", link_preview=False, buttons=buttons)
    except Exception:
        await event.respond(text, parse_mode="html", link_preview=False, buttons=buttons)


async def rl_check(event, key: str, limit=5, per=10) -> bool:
    ok = await ratelimit.allow(f"{event.sender_id}:{key}", limit, per)
    if not ok:
        await edit_safe(event, "⏳ Çox sürətli! Bir az gözləyin.")
    return ok


async def tag_rl_check(sender_id: int) -> bool:
    return await ratelimit.allow(f"tag:{sender_id}", 1, 2)


async def get_target_user(event):
    arg = event.pattern_match.group(1).strip() if event.pattern_match else ""
    if event.is_reply:
        msg = await event.get_reply_message()
        return msg.sender_id, msg.sender
    if not arg:
        return None, None
    arg = arg.split()[0]
    try:
        if arg.isdigit() or (arg.startswith("-") and arg[1:].isdigit()):
            ent = await event.client.get_entity(int(arg))
        else:
            ent = await event.client.get_entity(arg.lstrip("@"))
        return ent.id, ent
    except Exception:
        return None, None


def _tag_buttons() -> list[list[Button]]:
    return [
        [Button.inline("🎯 Solo", b"tag:solo"), Button.inline("⚡ Trio", b"tag:trio")],
        [Button.inline("🔥 Five", b"tag:five"), Button.inline("🌊 Wave", b"tag:wave")],
        [Button.inline("🎲 Random", b"tag:random")],
    ]


def _build_mentions(users: Iterable, *, chunk_size: int) -> list[str]:
    chunk: list[str] = []
    messages: list[str] = []
    for user in users:
        chunk.append(f"<a href='tg://user?id={user.id}'>{user.first_name or 'user'}</a>")
        if len(chunk) >= chunk_size:
            messages.append(" ".join(chunk))
            chunk = []
    if chunk:
        messages.append(" ".join(chunk))
    return messages


async def _run_tag_mode(event, mode_key: str):
    mode = TAG_MODES[mode_key]
    members = []
    async for user in event.client.iter_participants(event.chat_id, limit=mode.max_users * 3):
        if user.bot or user.deleted:
            continue
        members.append(user)

    if not members:
        return await edit_safe(event, "⚠️ Tag üçün uyğun istifadəçi tapılmadı.")

    if mode.key == "random":
        random.shuffle(members)
    members = members[: mode.max_users]
    messages = _build_mentions(members, chunk_size=mode.chunk_size)

    try:
        await event.delete()
    except Exception:
        pass

    for idx, message in enumerate(messages, start=1):
        prefix = mode.header
        if mode.key == "wave":
            prefix = f"{mode.header} #{idx}"
        text = f"{prefix}\n{message}" if prefix else message
        try:
            await event.client.send_message(event.chat_id, text, parse_mode="html")
        except FloodWaitError as exc:
            await asyncio.sleep(exc.seconds + 1)
            await event.client.send_message(event.chat_id, text, parse_mode="html")
        await asyncio.sleep(mode.delay)


def register(client):
    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("alive")))
    async def alive(event):
        if not await rl_check(event, "alive"):
            return
        uptime = int(time.time() - START_TIME)
        msg = await db.get_setting("alive_msg") or (
            "✨ Raven Userbot \n"
            "━━━━━━━━━━━━━━━\n"
            "🤖 Sistem: <code>online</code>\n"
            "⚡ Versiya: <code>2.0.0</code>\n"
            f"⏱ Uptime: <code>{uptime}s</code>"
        )
        await edit_safe(event, msg)

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("dlive")))
    async def dlive(event):
        new = event.pattern_match.group(1).strip()
        if not new:
            return await edit_safe(event, f"ℹ️ İstifadə: <code>{P}dlive yeni mesaj</code>")
        await db.set_setting("alive_msg", new)
        await edit_safe(event, "✅ Alive mesajı yeniləndi.")

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("restart")))
    async def restart(event):
        await edit_safe(event, "♻️ Restart edilir...")
        os.environ["RESTART_CHAT"] = str(event.chat_id)
        os.environ["RESTART_MSG"] = str(event.id)
        os.execv(sys.executable, [sys.executable, *sys.argv])

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("help")))
    async def help_cmd(event):
        plugins = list(plugin_loader.loaded.keys())
        text = (
            "Raven Userbot\n"
            "━━━━━━━━━━━━━━━\n"
            "🛡 İdarəetmə:\n"
            "<code>.alive</code> | <code>.dlive</code> | <code>.restart</code>\n\n"
            "🔨 Moderasiya:\n"
            "<code>.ban</code> | <code>.unban</code> | <code>.mute</code> | <code>.block</code> | <code>.unblock</code>\n\n"
            "👤 İstifadəçi & Qrup:\n"
            "<code>.info</code> | <code>.tag</code> | <code>.setwelcome</code>\n\n"
            "🧬 Profil:\n"
            "<code>.klon</code> | <code>.unklon</code>\n\n"
            "🔌 Plugin sistemi: yalnız GitHub runtime sync\n"
            f"Repo: <code>{Config.PLUGIN_SOURCE_REPO or 'təyin edilməyib'}</code>\n"
            f"Path: <code>{Config.PLUGIN_SOURCE_PATH}</code>\n"
            "━━━━━━━━━━━━━━━\n"
            f"🔌 Aktiv Pluginlər ({len(plugins)}):\n"
            f"{', '.join(plugins) if plugins else 'Yoxdur'}"
        )
        await edit_safe(event, text)

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("ban")))
    async def ban(event):
        uid, _ = await get_target_user(event)
        if not uid:
            return await edit_safe(event, f"ℹ️ İstifadə: <code>{P}ban</code> (reply) və ya <code>{P}ban @user</code>")
        try:
            rights = ChatBannedRights(until_date=None, view_messages=True)
            await event.client(EditBannedRequest(event.chat_id, uid, rights))
            await edit_safe(event, f"🔨 Ban olundu: <code>{uid}</code>")
        except (ChatAdminRequiredError, UserAdminInvalidError):
            await edit_safe(event, "⚠️ Yetkiniz yoxdur.")
        except FloodWaitError as exc:
            await edit_safe(event, f"⏳ FloodWait: {exc.seconds} saniyə gözləyin")
        except Exception as exc:
            await edit_safe(event, f"❌ Xəta: {exc}")

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("unban")))
    async def unban(event):
        uid, _ = await get_target_user(event)
        if not uid:
            return await edit_safe(event, f"ℹ️ İstifadə: <code>{P}unban @user</code>")
        try:
            rights = ChatBannedRights(until_date=None, view_messages=False)
            await event.client(EditBannedRequest(event.chat_id, uid, rights))
            await edit_safe(event, f"✅ Ban açıldı: <code>{uid}</code>")
        except FloodWaitError as exc:
            await edit_safe(event, f"⏳ FloodWait: {exc.seconds} saniyə gözləyin")
        except Exception as exc:
            await edit_safe(event, f"❌ Xəta: {exc}")

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("mute")))
    async def mute(event):
        uid, _ = await get_target_user(event)
        if not uid:
            return await edit_safe(event, f"ℹ️ İstifadə: <code>{P}mute</code> (reply/id/username)")
        try:
            rights = ChatBannedRights(until_date=None, send_messages=True)
            await event.client(EditBannedRequest(event.chat_id, uid, rights))
            await edit_safe(event, f"🔇 Mute olundu: <code>{uid}</code>")
        except FloodWaitError as exc:
            await edit_safe(event, f"⏳ FloodWait: {exc.seconds} saniyə gözləyin")
        except Exception as exc:
            await edit_safe(event, f"❌ Xəta: {exc}")

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("block")))
    async def block(event):
        uid, _ = await get_target_user(event)
        if not uid:
            return await edit_safe(event, f"ℹ️ İstifadə: <code>{P}block</code> (reply/id/username)")
        try:
            await event.client(BlockRequest(uid))
            await db.add_block(uid)
            await edit_safe(event, f"⛔ Bloklandı: <code>{uid}</code>")
        except FloodWaitError as exc:
            await edit_safe(event, f"⏳ FloodWait: {exc.seconds} saniyə gözləyin")
        except Exception as exc:
            await edit_safe(event, f"❌ Xəta: {exc}")

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("unblock")))
    async def unblock(event):
        uid, _ = await get_target_user(event)
        if not uid:
            return await edit_safe(event, f"ℹ️ İstifadə: <code>{P}unblock</code>")
        try:
            await event.client(UnblockRequest(uid))
            await db.remove_block(uid)
            await edit_safe(event, f"✅ Blok açıldı: <code>{uid}</code>")
        except FloodWaitError as exc:
            await edit_safe(event, f"⏳ FloodWait: {exc.seconds} saniyə gözləyin")
        except Exception as exc:
            await edit_safe(event, f"❌ Xəta: {exc}")

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("info")))
    async def info(event):
        _, ent = await get_target_user(event)
        if not ent:
            ent = await event.get_sender()
        full = await event.client.get_entity(ent.id)
        try:
            fu = await event.client(GetFullUserRequest(full.id))
            bio = fu.full_user.about or "—"
        except Exception:
            bio = "—"
        premium = "✅" if getattr(full, "premium", False) else "❌"
        text = (
            "👤 İstifadəçi məlumatı\n"
            "━━━━━━━━━━━━━━━\n"
            f"🪪 Ad: {full.first_name or ''} {full.last_name or ''}\n"
            f"🔗 Username: @{full.username or '—'}\n"
            f"🆔 ID: <code>{full.id}</code>\n"
            f"💬 Bio: <i>{bio}</i>\n"
            f"⭐ Premium: {premium}"
        )
        await edit_safe(event, text)

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("tag")))
    async def tag(event):
        if not event.is_group:
            return await edit_safe(event, "⚠️ Yalnız qruplarda işləyir.")
        if not await tag_rl_check(event.sender_id):
            return await edit_safe(event, "⏳ .tag üçün 2 saniyə gözləyin.")

        raw = event.pattern_match.group(1).strip().lower()
        if not raw:
            menu = (
                "🏷 <b>Tag menu</b>\n"
                "━━━━━━━━━━━━━━━\n"
                "1. Solo — tək-tək mention\n"
                "2. Trio — 3 nəfərlik qruplar\n"
                "3. Five — 5 nəfərlik qruplar\n"
                "4. Wave — dalğa formasında böyük qruplar\n"
                "5. Random — qarışıq 20 nəfər\n\n"
                "Aşağıdakı düymələrdən seçin və ya <code>.tag solo</code> kimi yazın."
            )
            return await edit_safe(event, menu, buttons=_tag_buttons())

        aliases = {
            "mention": "solo",
            "1": "solo",
            "3": "trio",
            "5": "five",
        }
        mode_key = aliases.get(raw, raw)
        if mode_key not in TAG_MODES:
            return await edit_safe(event, "⚠️ Mövcud modlar: solo, trio, five, wave, random")
        await _run_tag_mode(event, mode_key)

    @client.on(events.CallbackQuery(pattern=rb"^tag:(solo|trio|five|wave|random)$"))
    async def tag_callback(event):
        if not await tag_rl_check(event.sender_id):
            return await event.answer("2 saniyə gözləyin", alert=True)
        mode_key = event.data.decode().split(":", 1)[1]
        await event.answer(f"{TAG_MODES[mode_key].title} başladı")
        await _run_tag_mode(event, mode_key)

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("setwelcome")))
    async def setwelcome(event):
        text = event.pattern_match.group(1).strip()
        if not text:
            return await edit_safe(event, f"ℹ️ İstifadə: <code>{P}setwelcome Salam {{mention}}, xoş gəldin</code>")
        await db.save_welcome(event.chat_id, text)
        await edit_safe(event, "✅ Xoş gəldin mesajı yaddaşda saxlanıldı.")

    @client.on(events.ChatAction())
    async def welcome_handler(event):
        if not event.user_added and not event.user_joined:
            return
        message = await db.get_welcome(event.chat_id)
        if not message:
            return
        try:
            user = await event.get_user()
            mention = f"<a href='tg://user?id={user.id}'>{user.first_name or 'dost'}</a>"
            msg = message.replace("{mention}", mention).replace("{name}", user.first_name or "")
            await event.client.send_message(event.chat_id, msg, parse_mode="html")
        except Exception as exc:
            log.warning("welcome err: %s", exc)

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("klon")))
    async def klon(event):
        _, ent = await get_target_user(event)
        if not ent:
            return await edit_safe(event, f"ℹ️ İstifadə: <code>{P}klon</code> (reply və ya id)")
        await edit_safe(event, "🧬 Klonlanır...")
        me = await event.client.get_me()
        full_me = await event.client(GetFullUserRequest(me.id))
        photo_bytes = b""
        try:
            buf = io.BytesIO()
            await event.client.download_profile_photo("me", file=buf)
            photo_bytes = buf.getvalue()
        except Exception:
            pass

        await db.save_clone(
            me.id,
            me.first_name or "",
            me.last_name or "",
            full_me.full_user.about or "",
            photo_bytes,
        )

        target_full = await event.client(GetFullUserRequest(ent.id))
        try:
            await event.client(
                UpdateProfileRequest(
                    first_name=ent.first_name or "",
                    last_name=ent.last_name or "",
                    about=(target_full.full_user.about or "")[:70],
                )
            )
            buf = io.BytesIO()
            await event.client.download_profile_photo(ent.id, file=buf)
            buf.seek(0)
            if buf.getvalue():
                file = await event.client.upload_file(buf, file_name="klon.jpg")
                await event.client(UploadProfilePhotoRequest(file))
            await edit_safe(event, f"✅ Klonlama tamamlandı: {ent.first_name}")
        except FloodWaitError as exc:
            await edit_safe(event, f"⏳ FloodWait: {exc.seconds} saniyə gözləyin")
        except Exception as exc:
            await edit_safe(event, f"❌ Xəta: {exc}")

    @client.on(events.NewMessage(outgoing=True, pattern=cmd_re("unklon")))
    async def unklon(event):
        me = await event.client.get_me()
        row = await db.get_clone(me.id)
        if not row:
            return await edit_safe(event, "ℹ️ Klon məlumatı tapılmadı.")
        try:
            await event.client(
                UpdateProfileRequest(
                    first_name=row.original_first or "",
                    last_name=row.original_last or "",
                    about=row.original_bio or "",
                )
            )
            photos = await event.client(GetUserPhotosRequest(me.id, offset=0, max_id=0, limit=1))
            if photos.photos:
                await event.client(DeletePhotosRequest([photos.photos[0]]))
            if row.original_photo:
                buf = io.BytesIO(row.original_photo)
                file = await event.client.upload_file(buf, file_name="orig.jpg")
                await event.client(UploadProfilePhotoRequest(file))
            await db.delete_clone(me.id)
            await edit_safe(event, "✅ Original profil geri qaytarıldı.")
        except FloodWaitError as exc:
            await edit_safe(event, f"⏳ FloodWait: {exc.seconds} saniyə gözləyin")
        except Exception as exc:
            await edit_safe(event, f"❌ Xəta: {exc}")

    log.info("🚀 Raven Userbot komandaları qeydiyyatdan keçdi.")
