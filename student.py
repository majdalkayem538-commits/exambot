"""شغل الطالب: قائمة الاختبارات، فتح اختبار، زر «لقد أنهيت الحل»."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import subject_name, teacher_title
from database import execute, find_attempt, get_attempt, get_file, get_request, now, set_attempt, user_requests
from keyboards import BTN_REGISTER, BTN_STATUS, exams_menu, finish_button, main_keyboard
from utils import e, send_stored_file


async def my_exams(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    approved = [r for r in user_requests(update.effective_user.id) if r["status"] == "approved"]
    if not approved:
        await update.message.reply_text(
            "ما عندك تسجيل مقبول لسّا.\n"
            f"سجّل من «{BTN_REGISTER}»، أو استنى موافقة الآنسة وشوف حالتك من «{BTN_STATUS}»."
        )
        return
    if len(approved) == 1:
        text, kb = exams_menu(approved[0])
        await update.message.reply_text(text, reply_markup=kb)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📚 {subject_name(r['subject'])} — {teacher_title(r['subject'])}",
                              callback_data=f"menu:{r['id']}:0")]
        for r in approved
    ])
    await update.message.reply_text("لأي مادة؟", reply_markup=kb)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    req = get_request(int(parts[1]))
    page = int(parts[2]) if len(parts) > 2 else 0
    if not req or req["user_id"] != query.from_user.id or req["status"] != "approved":
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    await query.answer()
    text, kb = exams_menu(req, page)
    try:
        await query.edit_message_text(text, reply_markup=kb)  # تقليب الصفحات بنفس الرسالة
    except BadRequest:
        await context.bot.send_message(query.from_user.id, text, reply_markup=kb)


async def open_exam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, req_id, num = query.data.split(":")
    req, num = get_request(int(req_id)), int(num)
    if not req or req["user_id"] != query.from_user.id or req["status"] != "approved":
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    att = find_attempt(req["id"], num)
    title = f"{subject_name(req['subject'])} — اختبار {num}"

    if att and att["status"] == "waiting":
        await query.answer("خلصت هاد الاختبار ⏳ الحلول بتوصلك أول ما الآنسة ترفعها.", show_alert=True)
        return
    if att and att["status"] == "done":
        sol = get_file(req["subject"], num, "solution")
        if not sol:
            await query.answer("ملف الحلول مو متوفر حالياً.", show_alert=True)
            return
        await query.answer()
        await send_stored_file(context.bot, req["user_id"], sol, f"✅ حلول {e(title)}")
        return

    exam = get_file(req["subject"], num, "exam")
    if not exam:
        await query.answer("هاد الاختبار مو متوفر.", show_alert=True)
        return
    await query.answer()
    if not att:
        att_id = execute(
            "INSERT INTO attempts(request_id, exam_num, status, created_at, updated_at) VALUES (?, ?, 'exam_sent', ?, ?)",
            (req["id"], num, now(), now()),
        )
    else:
        att_id = att["id"]
    await send_stored_file(
        context.bot, req["user_id"], exam,
        f"📝 {e(title)}\n\nبعد ما تخلص الحل اضغط الزر تحت لتوصلك الحلول 👇",
        reply_markup=finish_button(att_id),
    )


async def finish_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أول ضغطة: تأكيد، لحتى ما ينضغط بالغلط."""
    query = update.callback_query
    att = get_attempt(int(query.data.split(":")[1]))
    if not att or att["user_id"] != query.from_user.id:
        await query.answer("غير صالح.", show_alert=True)
        return
    if att["status"] != "exam_sent":
        await query.answer("هاد الاختبار منتهي من قبل ✅", show_alert=True)
        return
    await query.answer()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم، خلصت", callback_data=f"fin_yes:{att['id']}"),
        InlineKeyboardButton("↩️ لا، لسّا", callback_data=f"fin_no:{att['id']}"),
    ]])
    try:
        await query.edit_message_reply_markup(kb)
    except BadRequest:
        pass


async def finish_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    att_id = int(query.data.split(":")[1])
    await query.answer("تمام، كمّل حل 💪")
    try:
        await query.edit_message_reply_markup(finish_button(att_id))
    except BadRequest:
        pass


async def finish_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    att = get_attempt(int(query.data.split(":")[1]))
    if not att or att["user_id"] != query.from_user.id or att["req_status"] != "approved":
        await query.answer("غير صالح.", show_alert=True)
        return
    if att["status"] != "exam_sent":
        await query.answer("هاد الاختبار منتهي من قبل ✅", show_alert=True)
        return
    await query.answer()
    try:
        await query.edit_message_reply_markup(None)
    except BadRequest:
        pass

    num, subj = att["exam_num"], att["subject"]
    sol = get_file(subj, num, "solution")
    if not sol:
        set_attempt(att["id"], "waiting")
        await context.bot.send_message(
            att["user_id"],
            f"👍 تم تسجيل إنك خلصت اختبار {num}.\n"
            "⏳ الآنسة لسّا ما رفعت الحلول، ورح يوصلوك هون تلقائياً أول ما ترفعها.",
        )
        return
    await send_stored_file(
        context.bot, att["user_id"], sol, f"✅ حلول {e(subject_name(subj))} — اختبار {num}"
    )
    set_attempt(att["id"], "done")


async def student_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ما في داعي تبعت صور 🙂\n"
        "بعد ما تخلص الاختبار اضغط زر «✅ لقد أنهيت الحل» يلي تحت ملف الاختبار، وبتوصلك الحلول.",
        reply_markup=main_keyboard(update.effective_user.id),
    )
