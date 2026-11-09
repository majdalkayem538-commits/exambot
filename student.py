"""شغل الطالب: قائمة الاختبارات، فتح اختبار، زر «لقد أنهيت الحل»، تسجيل الدرجة، حالة الاشتراك.
كل الرسائل للطالب بالعربية الفصحى."""

from __future__ import annotations

import math
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config import MIN_SOLVE_MINUTES, REQ_LABELS_STUDENT, log, subject_name, teacher_title
from database import (execute, exam_label, find_attempt, get_attempt, get_file, get_request, is_active,
                      minutes_since, move_attempt, now, set_score, user_requests)
from keyboards import (BTN_REGISTER, BTN_STATUS, exams_menu, finish_button, main_keyboard, renew_button,
                       score_button)
from subscription import expiry_text_student
from utils import e
from delivery import send_exam, send_solution

FILE_ERROR = "⚠️ تعذّر إرسال الملف الآن. يُرجى المحاولة بعد قليل، وإن تكررت المشكلة فأبلغ الآنسة."
SCORE_MAX = 100


def own_active_request(req, user_id: int) -> bool:
    return bool(req) and req["user_id"] == user_id and is_active(req)


def own_attempt(att, user_id: int) -> bool:
    return bool(att) and att["user_id"] == user_id and is_active(
        {"status": att["req_status"], "expires_at": att["expires_at"]}
    )


async def inactive_answer(query, req) -> None:
    """رد مناسب إذا الطالب ما عندو وصول (منتهي، موقوف...)."""
    if req and req["user_id"] == query.from_user.id and req["status"] in ("expired", "approved"):
        await query.answer("⌛ انتهى اشتراكك. اضغط «📋 حالة اشتراكي» لطلب التجديد.", show_alert=True)
    else:
        await query.answer("هذا الطلب غير صالح.", show_alert=True)


# ---------------------------------------------------------------- حالة الاشتراك
def status_text(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    reqs = user_requests(user_id)
    if not reqs:
        return f"لا يوجد لديك أي طلب حتى الآن.\nاضغط «{BTN_REGISTER}» للبدء.", None
    lines, buttons = ["<b>اشتراكاتك:</b>"], []
    for r in reqs[:5]:
        status = r["status"]
        if status == "approved" and not is_active(r):
            status = "expired"
        lines.append(f"\n<b>{e(subject_name(r['subject']))}</b> — {e(teacher_title(r['subject']))}")
        lines.append(f"الحالة: {REQ_LABELS_STUDENT[status]} (طلب رقم {r['id']})")
        if status in ("approved", "expired"):
            lines.append(expiry_text_student(r))
            if status == "expired" or (r["expires_at"] and is_active(r)):
                buttons.append([InlineKeyboardButton(
                    f"🔄 تجديد اشتراك {subject_name(r['subject'])}", callback_data=f"renew:{r['id']}")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons) if buttons else None


async def my_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, kb = status_text(update.effective_user.id)
    await update.message.reply_text(text, reply_markup=kb)


# ---------------------------------------------------------------- الاختبارات
async def my_exams(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reqs = user_requests(update.effective_user.id)
    active = [r for r in reqs if is_active(r)]
    if not active:
        ended = [r for r in reqs if r["status"] in ("approved", "expired")]
        if ended:
            await update.message.reply_text(
                f"⌛ انتهى اشتراكك في مادة {e(subject_name(ended[0]['subject']))}.\n"
                "لتجديده اضغط الزر أدناه.",
                reply_markup=renew_button(ended[0]["id"]),
            )
            return
        if any(r["status"] == "revoked" for r in reqs):
            revoked = next(r for r in reqs if r["status"] == "revoked")
            await update.message.reply_text(
                f"🚫 تم إيقاف اشتراكك. للاستفسار تواصل مع {e(teacher_title(revoked['subject']))}."
            )
            return
        await update.message.reply_text(
            "لا يوجد لديك اشتراك مقبول حتى الآن.\n"
            f"سجّل عبر «{BTN_REGISTER}»، أو انتظر موافقة الآنسة وتابع حالتك عبر «{BTN_STATUS}»."
        )
        return
    if len(active) == 1:
        text, kb = exams_menu(active[0])
        await update.message.reply_text(text, reply_markup=kb)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📚 {subject_name(r['subject'])} — {teacher_title(r['subject'])}",
                              callback_data=f"menu:{r['id']}:0")]
        for r in active
    ])
    await update.message.reply_text("اختر المادة:", reply_markup=kb)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    req = get_request(int(parts[1]))
    page = int(parts[2]) if len(parts) > 2 else 0
    if not own_active_request(req, query.from_user.id):
        await inactive_answer(query, req)
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
    if not own_active_request(req, query.from_user.id):
        await inactive_answer(query, req)
        return
    att = find_attempt(req["id"], num)
    title = f"{subject_name(req['subject'])} — {exam_label(req['subject'], num)}"

    if att and att["status"] == "waiting":
        await query.answer("لقد أنهيت هذا الاختبار ⏳ وستصلك الحلول فور رفعها من قِبل الآنسة.", show_alert=True)
        return
    if att and att["status"] == "done":
        sol = get_file(req["subject"], num, "solution")
        if not sol:
            await query.answer("ملف الحلول غير متوفر حاليًا.", show_alert=True)
            return
        await query.answer()
        await send_solution(context.bot, req["user_id"], req["subject"], num, sol,
                            req["full_name"], req["card_number"], f"✅ حلول {e(title)}")
        return

    exam = get_file(req["subject"], num, "exam")
    if not exam:
        await query.answer("هذا الاختبار غير متوفر.", show_alert=True)
        return
    await query.answer()
    if not att:
        execute(
            "INSERT OR IGNORE INTO attempts(request_id, exam_num, status, created_at, updated_at)"
            " VALUES (?, ?, 'exam_sent', ?, ?)",
            (req["id"], num, now(), now()),
        )
        att = find_attempt(req["id"], num)
    hint = f"\n⏱ يُفعَّل زر «لقد أنهيت الحل» بعد {MIN_SOLVE_MINUTES} دقيقة من فتح الاختبار." if MIN_SOLVE_MINUTES else ""
    try:
        await context.bot.send_chat_action(req["user_id"], ChatAction.UPLOAD_PHOTO)
        await send_exam(context.bot, req["user_id"], req["subject"], num, exam, f"📝 {e(title)}")
    except TelegramError as err:
        log.warning("exam send failed for request %s exam %s: %s", req["id"], num, err)
        await context.bot.send_message(req["user_id"], FILE_ERROR)
        return
    await context.bot.send_message(
        req["user_id"],
        f"📝 {e(title)}\nبعد الانتهاء من الحل اضغط الزر أدناه لتصلك الحلول 👇{hint}",
        reply_markup=finish_button(att["id"]),
    )


def minutes_left(att) -> int:
    """كم دقيقة باقية قبل ما يتفعّل زر «لقد أنهيت الحل» (0 = مسموح)."""
    if not MIN_SOLVE_MINUTES:
        return 0
    return max(0, math.ceil(MIN_SOLVE_MINUTES - minutes_since(att["created_at"])))


async def finish_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أول ضغطة: تأكيد، لحتى ما ينضغط بالغلط."""
    query = update.callback_query
    att = get_attempt(int(query.data.split(":")[1]))
    if not own_attempt(att, query.from_user.id):
        await query.answer("غير صالح.", show_alert=True)
        return
    if att["status"] != "exam_sent":
        await query.answer("لقد أنهيت هذا الاختبار سابقًا ✅", show_alert=True)
        return
    left = minutes_left(att)
    if left:
        await query.answer(f"⏱ ما زال الوقت مبكرًا! خصّص وقتًا كافيًا للحل.\nيُفعَّل الزر بعد {left} دقيقة.",
                           show_alert=True)
        return
    await query.answer()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم، أنهيت", callback_data=f"fin_yes:{att['id']}"),
        InlineKeyboardButton("↩️ لا، ليس بعد", callback_data=f"fin_no:{att['id']}"),
    ]])
    try:
        await query.edit_message_reply_markup(kb)
    except BadRequest:
        pass


async def finish_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    att = get_attempt(int(query.data.split(":")[1]))
    if not own_attempt(att, query.from_user.id):
        await query.answer("غير صالح.", show_alert=True)
        return
    await query.answer("حسنًا، تابع الحل 💪")
    try:
        await query.edit_message_reply_markup(finish_button(att["id"]))
    except BadRequest:
        pass


async def finish_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    att = get_attempt(int(query.data.split(":")[1]))
    if not own_attempt(att, query.from_user.id):
        await query.answer("غير صالح.", show_alert=True)
        return
    if att["status"] != "exam_sent":
        await query.answer("لقد أنهيت هذا الاختبار سابقًا ✅", show_alert=True)
        return
    left = minutes_left(att)
    if left:
        await query.answer(f"⏱ يُفعَّل الزر بعد {left} دقيقة.", show_alert=True)
        return

    num, subj = att["exam_num"], att["subject"]
    sol = get_file(subj, num, "solution")
    # تغيير ذرّي: إذا انضغط الزر مرتين، بس أول ضغطة بتنجح
    if not move_attempt(att["id"], "done" if sol else "waiting", "exam_sent"):
        await query.answer("لقد أنهيت هذا الاختبار سابقًا ✅", show_alert=True)
        return
    await query.answer()
    try:
        await query.edit_message_reply_markup(None)
    except BadRequest:
        pass

    if not sol:
        await context.bot.send_message(
            att["user_id"],
            f"👍 سُجِّل إنهاؤك للاختبار {num}.\n⏳ لم ترفع الآنسة الحلول بعد، وستصلك هنا تلقائيًا فور رفعها.",
        )
        return
    try:
        await context.bot.send_chat_action(att["user_id"], ChatAction.UPLOAD_PHOTO)
        await send_solution(context.bot, att["user_id"], subj, num, sol, att["full_name"], att["card_number"],
                            f"✅ حلول {e(subject_name(subj))} — {e(exam_label(subj, num))}")
    except TelegramError as err:
        log.warning("solution send failed for attempt %s: %s", att["id"], err)
        move_attempt(att["id"], "waiting", "done")  # بيرجع "بانتظار"، وبيوصلو أول ما تنرفع الحلول من جديد
        await context.bot.send_message(att["user_id"], FILE_ERROR)
        return
    await ask_score(context, att["user_id"], att["id"], num)


# ---------------------------------------------------------------- الدرجات
async def ask_score(context: ContextTypes.DEFAULT_TYPE, user_id: int, att_id: int, num: int) -> None:
    await context.bot.send_message(
        user_id,
        f"📝 بعد تصحيح إجاباتك من ملف الحلول، يمكنك تسجيل درجتك في الاختبار {num} (من {SCORE_MAX}) "
        "لمتابعة مستواك.",
        reply_markup=score_button(att_id),
    )


async def score_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    att = get_attempt(int(query.data.split(":")[1]))
    if not att or att["user_id"] != query.from_user.id or att["status"] != "done":
        await query.answer("غير صالح.", show_alert=True)
        return
    await query.answer()
    context.user_data["await"] = {"kind": "score", "att": att["id"], "t": time.time()}
    await context.bot.send_message(query.from_user.id, f"اكتب درجتك في الاختبار {att['exam_num']} (رقم من 0 إلى {SCORE_MAX}):")


async def score_received(update: Update, context: ContextTypes.DEFAULT_TYPE, wait: dict) -> None:
    text = (update.message.text or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    att = get_attempt(wait["att"])
    if not att or att["user_id"] != update.effective_user.id:
        return
    if not text.isdigit() or not 0 <= int(text) <= SCORE_MAX:
        context.user_data["await"] = wait
        await update.message.reply_text(f"⚠️ يُرجى كتابة رقم من 0 إلى {SCORE_MAX} فقط.")
        return
    set_score(att["id"], int(text))
    await update.message.reply_text(
        f"✅ سُجّلت درجتك ({text}) في الاختبار {att['exam_num']}. بالتوفيق! 🌟",
        reply_markup=main_keyboard(update.effective_user.id),
    )


async def student_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "لا حاجة لإرسال صور أو ملفات 🙂\n"
        "بعد الانتهاء من الاختبار اضغط زر «✅ لقد أنهيت الحل» أسفل ملف الاختبار، وستصلك الحلول.",
        reply_markup=main_keyboard(update.effective_user.id),
    )
