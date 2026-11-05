"""شغل الآنسة: قبول/رفض التسجيل، رفع الاختبارات والحلول، عناوين الاختبارات، حذف اختبار، الطلبات المعلّقة.
(الإحصائيات بملف stats.py، الاشتراكات بـ subscription.py، الرسائل الجماعية بـ broadcast.py)"""

from __future__ import annotations

import asyncio
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config import ADMIN_SUBJECTS, REQ_LABELS, SUBJECTS, TITLE_MAX_LEN, UPLOAD_TIMEOUT_MIN, log, subject_name
from database import (delete_exam, exam_label, get_file, get_request, next_exam_num, notify_new_exams, q_all,
                      save_file, set_attempt, set_exam_title, set_request_status)
from keyboards import (BTN_REGISTER, BTN_UP_EXAM, BTN_UP_SOL, KIND_LABEL, exam_picker, main_keyboard,
                       reg_buttons, subject_chooser, upload_menu)
from utils import close_admin_card, e, safe_send, student_card


def owns(user_id: int, subject: str) -> bool:
    return subject in ADMIN_SUBJECTS.get(user_id, [])


# ---------------------------------------------------------------- قبول / رفض
async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, raw_id = query.data.split(":")
    req = get_request(int(raw_id))
    if not req or req["subject"] not in SUBJECTS or SUBJECTS[req["subject"]]["admin"] != query.from_user.id:
        await query.answer("هاد الطلب مو لمادتك.", show_alert=True)
        return
    if req["status"] != "pending":
        await query.answer("تم التعامل مع هاد الطلب من قبل.", show_alert=True)
        await close_admin_card(query, f"ℹ️ الحالة: {REQ_LABELS.get(req['status'], req['status'])}")
        return
    if action == "reg_ok":  # القبول بيمر باختيار المدة
        from subscription import show_approve_picker
        await query.answer("اختاري مدة الاشتراك")
        await show_approve_picker(query, req)
        return
    if not set_request_status(req["id"], "rejected", only_from=("pending",)):
        await query.answer("تم التعامل مع هاد الطلب من قبل.", show_alert=True)
        return
    await query.answer()
    await safe_send(
        context.bot, req["user_id"],
        f"❌ نعتذر، رُفض طلبك رقم {req['id']} في مادة {e(subject_name(req['subject']))}.\n"
        f"إن كان هناك خطأ في البيانات يمكنك تقديم طلب جديد عبر «{BTN_REGISTER}».",
    )
    await close_admin_card(query, "❌ <b>تم الرفض</b>")


async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    subjects = ADMIN_SUBJECTS[update.effective_user.id]
    marks = ",".join("?" * len(subjects))
    regs = q_all(
        f"SELECT * FROM requests WHERE subject IN ({marks}) AND status = 'pending' ORDER BY id LIMIT 30", subjects
    )
    if not regs:
        await update.message.reply_text("ما في طلبات تسجيل معلّقة 👌")
        return
    for r in regs:
        await update.message.reply_text(
            student_card(r, f"📥 طلب تسجيل #{r['id']}", warn_shared_card=True), reply_markup=reg_buttons(r["id"])
        )


# ---------------------------------------------------------------- رفع الملفات
async def send_upload_menu(bot, chat_id: int, subject: str, kind: str) -> None:
    text, kb = upload_menu(subject, kind)
    await bot.send_message(chat_id, text, reply_markup=kb)


async def set_file_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    kind = "solution" if (text == BTN_UP_SOL or "solution" in text) else "exam"
    context.user_data.pop("upload", None)
    subjects = ADMIN_SUBJECTS[update.effective_user.id]
    if len(subjects) == 1:
        await send_upload_menu(context.bot, update.effective_chat.id, subjects[0], kind)
        return
    await update.message.reply_text("لأي مادة؟", reply_markup=subject_chooser(f"up:{kind}", subjects))


async def upload_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, kind, subject = query.data.split(":")
    if not owns(query.from_user.id, subject):
        await query.answer("مو مادتك.", show_alert=True)
        return
    await query.answer()
    await send_upload_menu(context.bot, query.from_user.id, subject, kind)


async def upload_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, kind, subject, page = query.data.split(":")
    if not owns(query.from_user.id, subject):
        await query.answer("مو مادتك.", show_alert=True)
        return
    await query.answer()
    text, kb = upload_menu(subject, kind, int(page))
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest:
        pass


async def upload_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """upn:<kind>:<subject>:<رقم أو new>. "new" بيتحدد رقمه وقت ما يوصل الملف، لحتى زر قديم ما يكتب فوق اختبار موجود."""
    query = update.callback_query
    _, kind, subject, num = query.data.split(":")
    if not owns(query.from_user.id, subject):
        await query.answer("مو مادتك.", show_alert=True)
        return
    if num == "new":
        if kind != "exam":
            await query.answer("غير صالح.", show_alert=True)
            return
        slot = "new"
        prompt = (f"ابعتيلي هلّق ملف <b>الاختبار الجديد</b> لمادة {e(subject_name(subject))}\n"
                  f"💡 إذا بدك عنوان للاختبار اكتبيه كتعليق (caption) على الملف (حتى {TITLE_MAX_LEN} حرف).")
    else:
        slot = int(num)
        if not get_file(subject, slot, "exam"):
            await query.answer("هاد الاختبار مو موجود. لإضافة اختبار جديد استخدمي زر ➕.", show_alert=True)
            return
        prompt = f"ابعتيلي هلّق ملف {KIND_LABEL[kind]} رقم <b>{slot}</b> لمادة {e(subject_name(subject))}"
        if get_file(subject, slot, kind):
            prompt += "\n⚠️ هاد الملف موجود، والملف يلي رح تبعتيه رح <b>يبدّله</b>."
    await query.answer()
    context.user_data.pop("await", None)
    context.user_data["upload"] = (subject, slot, kind, time.time())
    await context.bot.send_message(
        query.from_user.id, prompt + "\n(PDF أو صورة أو فيديو)\n\nللإلغاء اضغطي أي زر من تحت."
    )


async def deliver_waiting(context: ContextTypes.DEFAULT_TYPE, subject: str, num: int) -> tuple[int, int]:
    """بعد رفع ملف حلول: بيبعتو (معلّم باسم كل طالب) لكل يلي خلصوا الاختبار وكانوا ناطرين."""
    from student import ask_score
    from watermark import send_solution

    sol = get_file(subject, num, "solution")
    waiting = q_all(
        "SELECT a.id, r.user_id, r.full_name, r.card_number FROM attempts a JOIN requests r ON r.id = a.request_id"
        " WHERE r.subject = ? AND a.exam_num = ? AND a.status = 'waiting' AND r.status = 'approved'",
        (subject, num),
    )
    sent = failed = 0
    for w in waiting:
        ok = False
        for _ in range(2):  # إذا تلغرام طلب تهدئة، منستنى ومنعيد مرة
            try:
                await send_solution(context.bot, w["user_id"], subject, num, sol, w["full_name"], w["card_number"],
                                    f"✅ وصلت حلول {e(subject_name(subject))} — {e(exam_label(subject, num))}")
                ok = True
                break
            except TelegramError as err:
                wait = getattr(err, "retry_after", None)
                if wait is None:
                    log.warning("deliver to %s failed: %s", w["user_id"], err)
                    break
                await asyncio.sleep(float(getattr(wait, "total_seconds", lambda: wait)()) + 0.5)
        if ok:
            set_attempt(w["id"], "done")
            sent += 1
            try:
                await ask_score(context, w["user_id"], w["id"], num)
            except TelegramError:
                pass
        else:
            failed += 1
        await asyncio.sleep(0.05)  # لحتى ما نتجاوز حد تلغرام للرسائل
    return sent, failed


async def admin_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.user_data.pop("upload", None)
    msg = update.message
    if pending and time.time() - pending[3] > UPLOAD_TIMEOUT_MIN * 60:
        pending = None  # اختيار قديم، منطلب تختار من جديد لحتى الملف ما يروح لمكان غلط
    if not pending:
        await msg.reply_text(
            f"لرفع ملف اضغطي «{BTN_UP_EXAM}» أو «{BTN_UP_SOL}» واختاري الرقم أولاً.",
            reply_markup=main_keyboard(update.effective_user.id),
        )
        return
    subject, slot, kind, _ = pending
    if msg.document:
        file_id, ftype = msg.document.file_id, "document"
    elif msg.photo:
        file_id, ftype = msg.photo[-1].file_id, "photo"
    else:
        file_id, ftype = msg.video.file_id, "video"
    is_new = slot == "new"
    num = next_exam_num(subject) if is_new else slot
    save_file(subject, num, kind, file_id, ftype)
    note = ""
    title = " ".join((msg.caption or "").split())[:TITLE_MAX_LEN]
    if kind == "exam" and title:
        set_exam_title(subject, num, title)
        note += f"\n🏷 العنوان: {e(title)}"
    if kind == "solution":
        sent, failed = await deliver_waiting(context, subject, num)
        if sent:
            note += f"\n📨 انبعتت الحلول تلقائياً لـ {sent} طالب كانوا بانتظارها."
        if failed:
            note += f"\n⚠️ {failed} طالب ما وصلتهم (يمكن حاظرين البوت)."
    if kind == "exam" and is_new and notify_new_exams(subject):
        from broadcast import notify_new_exam
        n = await notify_new_exam(context, subject, num)
        note += f"\n🔔 وصل إشعار لـ {n} طالب."
    await msg.reply_text(f"✅ تم حفظ ملف {KIND_LABEL[kind]} رقم {num} لمادة {e(subject_name(subject))}.{note}")
    await send_upload_menu(context.bot, update.effective_chat.id, subject, kind)  # لترفعي اللي بعده بسرعة


# ---------------------------------------------------------------- عناوين الاختبارات وحذفها
async def exam_tools_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ttm/dlm:<subject>:<page> قوائم — ttl:<subject>:<num> تسمية — dln/dly:<subject>:<num> حذف."""
    query = update.callback_query
    kind, subject, value = query.data.split(":")
    if not owns(query.from_user.id, subject):
        await query.answer("مو مادتك.", show_alert=True)
        return
    back = f"upg:exam:{subject}:0"

    async def show(text, rows):
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        except BadRequest:
            pass

    if kind in ("ttm", "dlm"):
        await query.answer()
        prefix = "ttl" if kind == "ttm" else "dln"
        rows, _ = exam_picker(subject, prefix, int(value), back)
        what = "تعطيه عنوان أو تغيّري عنوانه" if kind == "ttm" else "تحذفيه"
        await show(f"اختاري رقم الاختبار يلي بدك {what}:", rows)
        return

    num = int(value)
    if not get_file(subject, num, "exam"):
        await query.answer("هاد الاختبار مو موجود.", show_alert=True)
        return
    if kind == "ttl":
        await query.answer()
        context.user_data.pop("upload", None)
        context.user_data["await"] = {"kind": "title", "subject": subject, "num": num, "t": time.time()}
        await context.bot.send_message(
            query.from_user.id,
            f"اكتبي عنوان {e(exam_label(subject, num))} (حتى {TITLE_MAX_LEN} حرف).\n"
            "لحذف العنوان ابعتي - (شرطة).\n\nللإلغاء اضغطي أي زر من تحت.",
        )
    elif kind == "dln":
        await query.answer()
        await show(
            f"🗑 متأكدة بدك تحذفي <b>{e(exam_label(subject, num))}</b> وحلوله؟\n"
            "ما رح يبيّن للطلاب بعد هيك، والرقم ما رح يرجع ينستخدم. محاولات الطلاب القديمة بتضل بالإحصائيات.",
            [[InlineKeyboardButton("🗑 نعم، احذفيه", callback_data=f"dly:{subject}:{num}"),
              InlineKeyboardButton("↩️ لا", callback_data=f"dlm:{subject}:0")]],
        )
    elif kind == "dly":
        label = exam_label(subject, num)
        delete_exam(subject, num)
        await query.answer(f"🗑 انحذف {label}", show_alert=True)
        text, kb = upload_menu(subject, "exam")
        try:
            await query.edit_message_text(text, reply_markup=kb)
        except BadRequest:
            pass


async def title_received(update: Update, context: ContextTypes.DEFAULT_TYPE, wait: dict) -> None:
    subject, num = wait["subject"], wait["num"]
    if not owns(update.effective_user.id, subject):
        return
    raw = " ".join((update.message.text or "").split())
    if not raw:
        context.user_data["await"] = wait
        await update.message.reply_text("⚠️ اكتبي العنوان كنص.")
        return
    title = None if raw == "-" else raw[:TITLE_MAX_LEN]
    set_exam_title(subject, num, title)
    await update.message.reply_text(
        f"✅ {'انحذف العنوان' if title is None else 'صار العنوان'}: {e(exam_label(subject, num))}",
        reply_markup=main_keyboard(update.effective_user.id),
    )
