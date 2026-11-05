"""الرسائل الجماعية: الآنسة بتبعت إعلان لكل طلاب مادتها، وإشعار تلقائي لما ينزل اختبار جديد."""

from __future__ import annotations

import asyncio
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from config import ADMIN_SUBJECTS, log, subject_name, teacher_title
from database import active_students, exam_label
from keyboards import BTN_EXAMS, subject_chooser
from utils import e


async def _send_all(subject: str, send) -> tuple[int, int]:
    """بيبعت لكل الطلاب الفعّالين بالمادة، مع احترام حد تلغرام. بيرجّع (وصل، فشل)."""
    ok = failed = 0
    for st in active_students(subject):
        for _ in range(2):
            try:
                await send(st["user_id"])
                ok += 1
                break
            except RetryAfter as err:
                wait = err.retry_after.total_seconds() if hasattr(err.retry_after, "total_seconds") else err.retry_after
                await asyncio.sleep(float(wait) + 0.5)
            except (Forbidden, BadRequest, TelegramError) as err:
                log.info("broadcast to %s failed: %s", st["user_id"], err)
                failed += 1
                break
        await asyncio.sleep(0.05)
    return ok, failed


async def notify_new_exam(context: ContextTypes.DEFAULT_TYPE, subject: str, num: int) -> int:
    text = (f"🔔 نُشر اختبار جديد في مادة <b>{e(subject_name(subject))}</b>: {e(exam_label(subject, num))}\n"
            f"اضغط «{BTN_EXAMS}» لفتحه.")
    ok, _ = await _send_all(subject, lambda uid: context.bot.send_message(uid, text))
    return ok


# ---------------------------------------------------------------- رسالة جماعية من الآنسة
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    subjects = ADMIN_SUBJECTS.get(update.effective_user.id, [])
    if len(subjects) == 1:
        await ask_message(context, update.effective_user.id, subjects[0])
    elif subjects:
        await update.message.reply_text("لطلاب أي مادة؟", reply_markup=subject_chooser("bcs", subjects))


async def ask_message(context: ContextTypes.DEFAULT_TYPE, user_id: int, subject: str) -> None:
    context.user_data.pop("upload", None)
    context.user_data["await"] = {"kind": "broadcast", "subject": subject, "t": time.time()}
    n = len(active_students(subject))
    await context.bot.send_message(
        user_id,
        f"📣 ابعتي هلّق الرسالة يلي بدك توصل لطلاب <b>{e(subject_name(subject))}</b> ({n} طالب مشترك).\n"
        "بتقدري تبعتي نص، صورة، ملف، أو رسالة صوتية. رح تشوفيها قبل الإرسال.\n\nللإلغاء اضغطي أي زر من تحت.",
    )


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """bcs:<subject> اختيار المادة — bcy تأكيد — bcn إلغاء."""
    query = update.callback_query
    parts = query.data.split(":")
    uid = query.from_user.id
    if parts[0] == "bcs":
        if parts[1] not in ADMIN_SUBJECTS.get(uid, []):
            await query.answer("مو مادتك.", show_alert=True)
            return
        await query.answer()
        await ask_message(context, uid, parts[1])
        return
    if uid not in ADMIN_SUBJECTS:
        await query.answer("مو مسموح.", show_alert=True)
        return
    draft = context.user_data.pop("bc_draft", None)
    if parts[0] == "bcn" or not draft:
        await query.answer("تم الإلغاء" if draft else "انتهت المسودة، ابعتي من جديد.")
        try:
            await query.edit_message_text("❌ انلغت الرسالة الجماعية.")
        except BadRequest:
            pass
        return
    subject = draft["subject"]
    if subject not in ADMIN_SUBJECTS.get(uid, []):
        await query.answer("مو مادتك.", show_alert=True)
        return
    await query.answer("عم ببعت...")
    try:
        await query.edit_message_text("⏳ عم ببعت الرسالة للطلاب...")
    except BadRequest:
        pass
    header = f"📣 رسالة من {teacher_title(subject)} — {subject_name(subject)}:"

    async def send(student_id: int):
        await context.bot.send_message(student_id, e(header))
        await context.bot.copy_message(student_id, uid, draft["message_id"])

    ok, failed = await _send_all(subject, send)
    await context.bot.send_message(uid, f"✅ وصلت الرسالة لـ {ok} طالب." + (f"\n⚠️ ما وصلت لـ {failed}." if failed else ""))


async def broadcast_received(update: Update, context: ContextTypes.DEFAULT_TYPE, wait: dict) -> None:
    """الآنسة بعتت الرسالة: منعرضلها معاينة مع تأكيد."""
    subject = wait["subject"]
    context.user_data["bc_draft"] = {"subject": subject, "message_id": update.message.message_id}
    n = len(active_students(subject))
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ ابعتي لـ {n} طالب", callback_data="bcy"),
        InlineKeyboardButton("❌ إلغاء", callback_data="bcn"),
    ]])
    await update.message.reply_text(
        f"👆 هي الرسالة رح توصل لكل طلاب {e(subject_name(subject))} المشتركين ({n}). متأكدة؟", reply_markup=kb
    )
