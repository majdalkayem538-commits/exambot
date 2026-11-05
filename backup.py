"""النسخ الاحتياطي: نسخة يومية من قاعدة البيانات بتنبعت لصاحب البوت (OWNER_ID)،
ونسخة يدوية بكبسة زر، واسترجاع نسخة قديمة إذا صار شي."""

from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config import DB_PATH, OWNER_ID, TZ, log
from database import db, init_db, q_one

REQUIRED_TABLES = {"requests", "attempts", "files"}
MAX_RESTORE_BYTES = 50 * 1024 * 1024


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID) and user_id == OWNER_ID


def snapshot() -> bytes:
    """نسخة متّسقة من قاعدة البيانات حتى لو البوت عم يكتب فيها (sqlite backup API)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "snap.db")
        with closing(db()) as src, closing(sqlite3.connect(path)) as dst:
            src.backup(dst)
        with open(path, "rb") as f:
            return f.read()


def summary() -> str:
    c = lambda sql: q_one(sql)["c"]  # noqa: E731
    return (f"👥 {c('SELECT COUNT(*) c FROM requests')} طلب | "
            f"📂 {c('SELECT COUNT(*) c FROM files')} ملف | "
            f"📝 {c('SELECT COUNT(*) c FROM attempts')} محاولة")


async def send_backup(bot, chat_id: int, title: str) -> None:
    data = snapshot()
    name = f"exam_bot_backup_{datetime.now(TZ):%Y-%m-%d_%H-%M}.db"
    await bot.send_document(
        chat_id, io.BytesIO(data), filename=name, protect_content=False,
        caption=f"{title}\n{summary()}\n\nاحتفظ فيها. لاسترجاعها: زر «♻️ استرجاع نسخة».",
    )


async def daily_backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not OWNER_ID:
        log.warning("daily backup skipped: OWNER_ID not set")
        return
    try:
        await send_backup(context.bot, OWNER_ID, "💾 النسخة الاحتياطية اليومية")
    except TelegramError as err:
        log.error("daily backup failed: %s", err)


async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    await send_backup(context.bot, update.effective_user.id, "💾 نسخة احتياطية (يدوية)")


async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    context.user_data["await"] = {"kind": "restore", "t": time.time()}
    await update.message.reply_text(
        "♻️ ابعتلي ملف النسخة الاحتياطية (.db) يلي بدك ترجعلها.\n"
        "⚠️ رح تنحذف البيانات الحالية وتتبدّل بالنسخة. قبل الاسترجاع رح ابعتلك نسخة من الوضع الحالي للأمان.\n\n"
        "للإلغاء اضغط أي زر من تحت."
    )


def _validate(path: str) -> str | None:
    """بيتأكد إنو الملف قاعدة بيانات البوت فعلاً. بيرجّع رسالة خطأ أو None."""
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return "الملف تالف."
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError:
        return "هاد الملف مو قاعدة بيانات."
    missing = REQUIRED_TABLES - tables
    return f"هاد الملف مو نسخة من البوت (ناقصه: {', '.join(missing)})." if missing else None


async def restore_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE, wait: dict) -> None:
    msg = update.message
    if not msg.document:
        context.user_data["await"] = wait
        await msg.reply_text("ابعت الملف كـ ملف (Document).")
        return
    if msg.document.file_size and msg.document.file_size > MAX_RESTORE_BYTES:
        await msg.reply_text("⚠️ الملف كبير كتير.")
        return
    tg_file = await msg.document.get_file()
    data = bytes(await tg_file.download_as_bytearray())
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "upload.db")
        with open(path, "wb") as f:
            f.write(data)
        error = _validate(path)
    if error:
        await msg.reply_text(f"❌ {error}")
        return
    context.user_data["restore_data"] = data
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("♻️ نعم، استرجع", callback_data="rsy"),
        InlineKeyboardButton("❌ إلغاء", callback_data="rsn"),
    ]])
    await msg.reply_text("✅ الملف سليم. متأكد بدك تبدّل كل البيانات الحالية بهالنسخة؟", reply_markup=kb)


async def restore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer("مو مسموح.", show_alert=True)
        return
    data = context.user_data.pop("restore_data", None)
    if query.data == "rsn" or not data:
        await query.answer("تم الإلغاء" if data else "انتهت العملية، ابعت الملف من جديد.")
        try:
            await query.edit_message_text("❌ انلغى الاسترجاع.")
        except BadRequest:
            pass
        return
    await query.answer("عم استرجع...")
    # نسخة أمان من الوضع الحالي قبل التبديل
    await send_backup(context.bot, query.from_user.id, "🛟 نسخة من البيانات قبل الاسترجاع (احتياط)")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "restore.db")
        with open(path, "wb") as f:
            f.write(data)
        with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(DB_PATH)) as dst:
            src.backup(dst)  # بيكتب فوق القاعدة الحالية بطريقة آمنة
    init_db()  # ترقية النسخة إذا كانت من إصدار أقدم
    log.info("database restored by owner")
    try:
        await query.edit_message_text(f"✅ تم الاسترجاع.\n{summary()}")
    except BadRequest:
        pass
