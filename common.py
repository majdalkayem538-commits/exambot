"""الشغلات المشتركة: /start، توجيه الأزرار الثابتة، والرسائل العامة."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS, ADMIN_SUBJECTS, REQ_LABELS, SUBJECTS, subject_name, teacher_title
from database import user_requests
from keyboards import (ADMIN_BTNS, BTN_EXAMS, BTN_PENDING, BTN_REGISTER, BTN_STATS, BTN_STATUS, BTN_UP_EXAM,
                       BTN_UP_SOL, main_keyboard)
from student import my_exams
from teacher import pending_cmd, set_file_cmd, stats_cmd
from utils import e


def status_text(user_id: int) -> str:
    reqs = user_requests(user_id)
    if not reqs:
        return f"ما عندك أي طلب لسّا.\nاضغط «{BTN_REGISTER}» تحت لتبلّش."
    return "<b>طلباتك:</b>\n" + "\n".join(
        f"#{r['id']} — {e(teacher_title(r['subject']))} ({e(subject_name(r['subject']))}): {REQ_LABELS[r['status']]}"
        for r in reqs[:5]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    kb = main_keyboard(user.id)
    if user.id in ADMIN_IDS:
        subs = ADMIN_SUBJECTS[user.id]
        teacher = SUBJECTS[subs[0]]["teacher"]
        names = "، ".join(subject_name(s) for s in subs)
        await update.message.reply_text(
            f"أهلاً آنسة {e(teacher)} 👋\nأنتِ مسؤولة عن: <b>{e(names)}</b>\n\n"
            f"{BTN_UP_EXAM} — إضافة اختبار جديد أو تبديل ملف اختبار\n"
            f"{BTN_UP_SOL} — رفع ملف حلول لأي اختبار\n"
            f"{BTN_PENDING} — طلبات التسجيل يلي بتستنى ردّك\n"
            f"{BTN_STATS} — إحصائيات وحالة الملفات\n\n"
            "طلبات التسجيل الجديدة بتوصلك هون تلقائياً مع أزرار قبول/رفض.",
            reply_markup=kb,
        )
        return

    if not user_requests(user.id):
        text = (
            "أهلاً بك في منصة الاختبارات 👋\n\n"
            f"اضغط «{BTN_REGISTER}» تحت لتسجّل عند الآنسة يلي بدك ياها."
        )
    else:
        text = status_text(user.id) + "\n\nاستخدم الأزرار تحت 👇"
    await update.message.reply_text(text, reply_markup=kb)


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بيوجّه ضغطة الزر الثابت للوظيفة المناسبة."""
    text = update.message.text
    uid = update.effective_user.id
    is_admin = uid in ADMIN_IDS
    if text in ADMIN_BTNS:
        if not is_admin:
            await update.message.reply_text("هاد الزر للآنسات بس.", reply_markup=main_keyboard(uid))
        elif text in (BTN_UP_EXAM, BTN_UP_SOL):
            await set_file_cmd(update, context)
        elif text == BTN_PENDING:
            await pending_cmd(update, context)
        else:
            await stats_cmd(update, context)
        return
    if is_admin:
        await update.message.reply_text("استخدمي الأزرار تحت 👇", reply_markup=main_keyboard(uid))
        return
    if text == BTN_EXAMS:
        await my_exams(update, context)
    elif text == BTN_STATUS:
        await update.message.reply_text(status_text(uid))


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("استخدم الأزرار تحت 👇", reply_markup=main_keyboard(update.effective_user.id))


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
