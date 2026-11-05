"""الشغلات المشتركة: /start، توجيه الأزرار الثابتة، واستقبال الردود يلي البوت ناطرها
(عدد أيام، عنوان اختبار، رسالة جماعية، بحث، بطاقات، درجة، ملف استرجاع)."""

from __future__ import annotations

import time

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS, ADMIN_SUBJECTS, OWNER_ID, SUBJECTS, subject_name
from database import user_requests
from keyboards import (ADMIN_BTNS, BTN_BACKUP, BTN_BROADCAST, BTN_EXAMS, BTN_PENDING, BTN_REGISTER, BTN_RESTORE,
                       BTN_SEARCH, BTN_SETTINGS, BTN_STATS, BTN_STATUS, BTN_UP_EXAM, BTN_UP_SOL, LEGACY, OWNER_BTNS,
                       main_keyboard)
from utils import e

AWAIT_TIMEOUT_MIN = 15


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID) and user_id == OWNER_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from student import status_text
    user = update.effective_user
    context.user_data.pop("await", None)
    kb = main_keyboard(user.id)
    if user.id in ADMIN_IDS:
        subs = ADMIN_SUBJECTS[user.id]
        teacher = SUBJECTS[subs[0]]["teacher"]
        names = "، ".join(subject_name(s) for s in subs)
        await update.message.reply_text(
            f"أهلاً آنسة {e(teacher)} 👋\nأنتِ مسؤولة عن: <b>{e(names)}</b>\n\n"
            f"{BTN_UP_EXAM} — إضافة اختبار جديد، تبديل ملف، عناوين، حذف\n"
            f"{BTN_UP_SOL} — رفع ملف حلول لأي اختبار\n"
            f"{BTN_PENDING} — طلبات التسجيل يلي بتستنى ردّك\n"
            f"{BTN_STATS} — طلابك، درجاتهم، اشتراكاتهم، وحالة الملفات\n"
            f"{BTN_BROADCAST} — إعلان لكل طلاب مادتك\n"
            f"{BTN_SEARCH} — لاقي طالب بالاسم أو رقم البطاقة\n"
            f"{BTN_SETTINGS} — مدة الاشتراك الافتراضية، القبول التلقائي، الإشعارات\n\n"
            "طلبات التسجيل الجديدة بتوصلك هون تلقائياً.",
            reply_markup=kb,
        )
        return
    if is_owner(user.id):
        await update.message.reply_text(
            "أهلاً 👋 أنت صاحب البوت.\n"
            f"{BTN_STATS} — إحصائيات كل المواد\n{BTN_SEARCH} — بالاسم أو رقم البطاقة\n"
            f"{BTN_BACKUP} — نسخة احتياطية هلّق (وبتوصلك وحدة كل يوم تلقائياً)\n"
            f"{BTN_RESTORE} — استرجاع نسخة قديمة",
            reply_markup=kb,
        )
        return

    if not user_requests(user.id):
        text = (
            "مرحبًا بك في منصة الاختبارات 👋\n\n"
            f"اضغط «{BTN_REGISTER}» أدناه للتسجيل لدى الآنسة التي تريدها."
        )
    else:
        text = status_text(user.id)[0] + "\n\nاستخدم الأزرار أدناه 👇"
    await update.message.reply_text(text, reply_markup=kb)


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بيوجّه ضغطة الزر الثابت للوظيفة المناسبة. أي زر بيلغي أي شي كان البوت ناطره."""
    from backup import backup_cmd, restore_cmd
    from broadcast import broadcast_cmd
    from settings import settings_cmd
    from stats import search_cmd, stats_cmd
    from student import my_exams, my_status
    from teacher import pending_cmd, set_file_cmd

    text = LEGACY.get(update.message.text, update.message.text)
    uid = update.effective_user.id
    is_admin, owner = uid in ADMIN_IDS, is_owner(uid)
    context.user_data.pop("upload", None)
    context.user_data.pop("await", None)

    if text in OWNER_BTNS:
        if owner:
            await (backup_cmd if text == BTN_BACKUP else restore_cmd)(update, context)
        else:
            await update.message.reply_text("هذا الزر غير متاح لك.", reply_markup=main_keyboard(uid))
        return
    if text in (BTN_STATS, BTN_SEARCH) and owner:
        await (stats_cmd if text == BTN_STATS else search_cmd)(update, context)
        return
    if text in ADMIN_BTNS:
        if not is_admin:
            await update.message.reply_text("هذا الزر مخصّص للآنسات فقط.", reply_markup=main_keyboard(uid))
            return
        handler = {
            BTN_UP_EXAM: set_file_cmd, BTN_UP_SOL: set_file_cmd, BTN_PENDING: pending_cmd, BTN_STATS: stats_cmd,
            BTN_BROADCAST: broadcast_cmd, BTN_SEARCH: search_cmd, BTN_SETTINGS: settings_cmd,
        }[text]
        await handler(update, context)
        return
    if is_admin or owner:
        await update.message.reply_text("استخدم الأزرار تحت 👇", reply_markup=main_keyboard(uid))
        return
    if text == BTN_EXAMS:
        await my_exams(update, context)
    elif text == BTN_STATUS:
        await my_status(update, context)


async def input_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أي رسالة (نص أو ملف) بعد ما البوت طلب شي معيّن. إذا ما في شي ناطره، بيرجع للسلوك العادي."""
    from backup import restore_file_received
    from broadcast import broadcast_received
    from settings import cards_received
    from stats import search_received
    from student import score_received, student_media
    from subscription import custom_days_received
    from teacher import admin_file, title_received

    uid = update.effective_user.id
    msg = update.message
    wait = context.user_data.pop("await", None)
    if wait and time.time() - wait.get("t", 0) > AWAIT_TIMEOUT_MIN * 60:
        wait = None
    kind = wait["kind"] if wait else None
    is_admin, owner = uid in ADMIN_IDS, is_owner(uid)
    has_file = bool(msg.document or msg.photo or msg.video)

    if kind == "restore" and owner:
        return await restore_file_received(update, context, wait)
    if kind == "broadcast" and is_admin:
        return await broadcast_received(update, context, wait)
    if kind == "cards" and is_admin and (msg.text or msg.document):
        return await cards_received(update, context, wait)
    if kind == "days" and (is_admin or owner) and msg.text:
        return await custom_days_received(update, context, wait)
    if kind == "title" and is_admin and msg.text:
        return await title_received(update, context, wait)
    if kind == "search" and (is_admin or owner) and msg.text:
        return await search_received(update, context, wait)
    if kind == "score" and msg.text:
        return await score_received(update, context, wait)

    # ما في شي ناطره: السلوك العادي
    if has_file and is_admin:
        return await admin_file(update, context)
    if has_file and not owner:
        return await student_media(update, context)
    if msg.text or has_file:
        await fallback_text(update, context)


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    staff = uid in ADMIN_IDS or is_owner(uid)
    await update.message.reply_text("استخدم الأزرار تحت 👇" if staff else "يُرجى استخدام الأزرار أدناه 👇",
                                    reply_markup=main_keyboard(uid))


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
