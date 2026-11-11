"""أدوات مساعدة: تنسيق النصوص وإرسال الملفات والرسائل."""

from __future__ import annotations

import asyncio
import html

from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from config import log, subject_name
from database import same_card_requests


def e(text) -> str:
    return html.escape(str(text or ""))


def student_card(row, title: str, warn_shared_card: bool = False) -> str:
    profile = f'<a href="tg://user?id={int(row["user_id"])}">فتح الحساب</a>'
    who = f"@{e(row['username'])} — {profile}" if row["username"] else profile
    text = (
        f"<b>{title}</b>\n"
        f"👤 الاسم: {e(row['full_name'])}\n"
        f"📚 المادة: {e(subject_name(row['subject']))}\n"
        f"🪪 بطاقة الاشتراك: <code>{e(row['card_number'])}</code>\n"
        + (f"📱 الهاتف: <code>{e(row['phone'])}</code> (مؤكَّد من تلغرام)\n" if row["phone"] else "")
        + f"🔗 الحساب: {who}\n"
        f"🕒 {e(row['updated_at'])}"
    )
    if warn_shared_card:
        others = same_card_requests(row["subject"], row["card_number"], row["user_id"])
        if others:
            names = "، ".join(f"{e(o['full_name'])} (#{o['id']})" for o in others[:5])
            text += (
                f"\n\n⚠️ <b>انتبهي:</b> نفس رقم البطاقة مستخدم من {len(others)} حساب تاني: {names}\n"
                "ممكن يكون في مشاركة اشتراك."
            )
    return text


async def send_stored_file(bot, chat_id: int, f, caption: str, reply_markup=None) -> None:
    if f["file_type"] == "photo":
        await bot.send_photo(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)
    elif f["file_type"] == "video":
        await bot.send_video(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)
    else:
        await bot.send_document(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)


async def send_file_with_retry(bot, chat_id: int, f, caption: str) -> bool:
    """للإرسال الجماعي: إذا تلغرام طلب ننطر (RetryAfter) منستنى ومنعيد مرة."""
    for _ in range(2):
        try:
            await send_stored_file(bot, chat_id, f, caption)
            return True
        except RetryAfter as err:
            wait = err.retry_after.total_seconds() if hasattr(err.retry_after, "total_seconds") else err.retry_after
            await asyncio.sleep(float(wait) + 0.5)
        except (Forbidden, BadRequest) as err:
            log.warning("send to %s failed: %s", chat_id, err)
            return False
        except TelegramError as err:
            log.warning("send to %s failed: %s", chat_id, err)
            return False
    return False


async def close_admin_card(query, note: str) -> None:
    """يشيل الأزرار من رسالة الآنسة ويضيف شو صار."""
    old = getattr(query.message, "text_html", None) or ""
    try:
        await query.edit_message_text(old + f"\n\n{note}", reply_markup=None)
    except (BadRequest, AttributeError):
        pass


async def safe_send(bot, chat_id: int, text: str, **kw) -> bool:
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except (Forbidden, BadRequest) as err:
        log.warning("send to %s failed: %s", chat_id, err)
        return False
