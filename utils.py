"""أدوات مساعدة: تنسيق النصوص وإرسال الملفات والرسائل."""

from __future__ import annotations

import html

from telegram.error import BadRequest, Forbidden

from config import log, subject_name


def e(text) -> str:
    return html.escape(str(text or ""))


def student_card(row, title: str) -> str:
    who = f"@{e(row['username'])}" if row["username"] else f"<code>{row['user_id']}</code>"
    return (
        f"<b>{title}</b>\n"
        f"👤 الاسم: {e(row['full_name'])}\n"
        f"📚 المادة: {e(subject_name(row['subject']))}\n"
        f"🪪 بطاقة الاشتراك: <code>{e(row['card_number'])}</code>\n"
        f"🔗 الحساب: {who}\n"
        f"🕒 {e(row['updated_at'])}"
    )


async def send_stored_file(bot, chat_id: int, f, caption: str, reply_markup=None) -> None:
    if f["file_type"] == "photo":
        await bot.send_photo(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)
    elif f["file_type"] == "video":
        await bot.send_video(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)
    else:
        await bot.send_document(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)


async def close_admin_card(query, note: str) -> None:
    """يشيل الأزرار من رسالة الآنسة ويضيف شو صار."""
    msg = query.message
    try:
        await query.edit_message_text((msg.text_html or "") + f"\n\n{note}", reply_markup=None)
    except BadRequest:
        pass


async def safe_send(bot, chat_id: int, text: str, **kw) -> bool:
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except (Forbidden, BadRequest) as err:
        log.warning("send to %s failed: %s", chat_id, err)
        return False
