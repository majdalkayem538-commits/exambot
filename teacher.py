"""شغل الآنسة: قبول/رفض التسجيل، رفع الاختبارات والحلول، الطلبات المعلّقة، الإحصائيات."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from config import ADMIN_SUBJECTS, REQ_LABELS, SUBJECTS, log, subject_name
from database import execute, get_file, get_request, now, q_all, set_attempt, uploaded_nums
from keyboards import (BTN_EXAMS, BTN_REGISTER, BTN_UP_EXAM, BTN_UP_SOL, KIND_LABEL, main_keyboard, reg_buttons,
                       upload_menu)
from utils import close_admin_card, e, safe_send, send_stored_file, student_card


async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, raw_id = query.data.split(":")
    req = get_request(int(raw_id))
    if not req or SUBJECTS[req["subject"]]["admin"] != query.from_user.id:
        await query.answer("هاد الطلب مو لمادتك.", show_alert=True)
        return
    if req["status"] != "pending":
        await query.answer("تم التعامل مع هاد الطلب من قبل.", show_alert=True)
        await close_admin_card(query, f"ℹ️ الحالة: {REQ_LABELS[req['status']]}")
        return
    await query.answer()
    if action == "reg_ok":
        execute("UPDATE requests SET status = 'approved', updated_at = ? WHERE id = ?", (now(), req["id"]))
        await safe_send(
            context.bot, req["user_id"],
            f"🎉 تمت الموافقة على طلبك #{req['id']} لمادة <b>{e(subject_name(req['subject']))}</b>.\n"
            f"اضغط «{BTN_EXAMS}» تحت لتشوف الاختبارات.",
            reply_markup=main_keyboard(req["user_id"]),
        )
        await close_admin_card(query, "✅ <b>تم القبول</b>")
    else:
        execute("UPDATE requests SET status = 'rejected', updated_at = ? WHERE id = ?", (now(), req["id"]))
        await safe_send(
            context.bot, req["user_id"],
            f"❌ تم رفض طلبك #{req['id']} لمادة {e(subject_name(req['subject']))}.\n"
            f"إذا في خطأ بالمعلومات فيك تقدّم طلب جديد من «{BTN_REGISTER}».",
        )
        await close_admin_card(query, "❌ <b>تم الرفض</b>")


async def send_upload_menu(message, subject: str, kind: str) -> None:
    text, kb = upload_menu(subject, kind)
    await message.reply_text(text, reply_markup=kb)


async def set_file_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    kind = "solution" if (text == BTN_UP_SOL or "solution" in text) else "exam"
    context.user_data.pop("upload", None)
    subjects = ADMIN_SUBJECTS[update.effective_user.id]
    if len(subjects) == 1:
        await send_upload_menu(update.message, subjects[0], kind)
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(subject_name(s), callback_data=f"up:{kind}:{s}")] for s in subjects])
    await update.message.reply_text("لأي مادة؟", reply_markup=kb)


async def upload_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, kind, subject = query.data.split(":")
    if subject not in ADMIN_SUBJECTS.get(query.from_user.id, []):
        await query.answer("مو مادتك.", show_alert=True)
        return
    await query.answer()
    await send_upload_menu(query.message, subject, kind)


async def upload_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, kind, subject, page = query.data.split(":")
    if subject not in ADMIN_SUBJECTS.get(query.from_user.id, []):
        await query.answer("مو مادتك.", show_alert=True)
        return
    await query.answer()
    text, kb = upload_menu(subject, kind, int(page))
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest:
        pass


async def upload_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, kind, subject, num = query.data.split(":")
    if subject not in ADMIN_SUBJECTS.get(query.from_user.id, []):
        await query.answer("مو مادتك.", show_alert=True)
        return
    num = int(num)
    if kind == "solution" and not get_file(subject, num, "exam"):
        await query.answer("هاد الاختبار مو موجود.", show_alert=True)
        return
    await query.answer()
    context.user_data["upload"] = (subject, num, kind)
    await query.message.reply_text(
        f"ابعتيلي هلّق ملف {KIND_LABEL[kind]} رقم <b>{num}</b> لمادة {e(subject_name(subject))} "
        "(PDF أو صورة أو فيديو)."
    )


async def deliver_waiting(context: ContextTypes.DEFAULT_TYPE, subject: str, num: int) -> int:
    """بعد رفع ملف حلول: بيبعتو لكل طالب خلص هاد الاختبار وكان بينطر."""
    sol = get_file(subject, num, "solution")
    waiting = q_all(
        "SELECT a.id, r.user_id FROM attempts a JOIN requests r ON r.id = a.request_id"
        " WHERE r.subject = ? AND a.exam_num = ? AND a.status = 'waiting' AND r.status = 'approved'",
        (subject, num),
    )
    sent = 0
    for w in waiting:
        try:
            await send_stored_file(
                context.bot, w["user_id"], sol, f"✅ وصلت حلول {e(subject_name(subject))} — اختبار {num}"
            )
            set_attempt(w["id"], "done")
            sent += 1
        except (Forbidden, BadRequest) as err:
            log.warning("deliver to %s failed: %s", w["user_id"], err)
    return sent


async def admin_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.user_data.pop("upload", None)
    msg = update.message
    if not pending:
        await msg.reply_text(
            f"لرفع ملف اضغطي «{BTN_UP_EXAM}» أو «{BTN_UP_SOL}» واختاري الرقم أولاً.",
            reply_markup=main_keyboard(update.effective_user.id),
        )
        return
    subject, num, kind = pending
    if msg.document:
        file_id, ftype = msg.document.file_id, "document"
    elif msg.photo:
        file_id, ftype = msg.photo[-1].file_id, "photo"
    else:
        file_id, ftype = msg.video.file_id, "video"
    execute(
        "INSERT OR REPLACE INTO files(subject, exam_num, kind, file_id, file_type) VALUES (?, ?, ?, ?, ?)",
        (subject, num, kind, file_id, ftype),
    )
    note = ""
    if kind == "solution":
        sent = await deliver_waiting(context, subject, num)
        if sent:
            note = f"\n📨 انبعتت الحلول تلقائياً لـ {sent} طالب كانوا بانتظارها."
    await msg.reply_text(f"✅ تم حفظ ملف {KIND_LABEL[kind]} رقم {num} لمادة {e(subject_name(subject))}.{note}")
    await send_upload_menu(msg, subject, kind)


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
        await update.message.reply_text(student_card(r, f"📥 طلب تسجيل #{r['id']}"), reply_markup=reg_buttons(r["id"]))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = []
    for s in ADMIN_SUBJECTS[update.effective_user.id]:
        reqs = {r["status"]: r["c"] for r in q_all(
            "SELECT status, COUNT(*) c FROM requests WHERE subject = ? GROUP BY status", (s,))}
        atts = {r["status"]: r["c"] for r in q_all(
            "SELECT a.status, COUNT(*) c FROM attempts a JOIN requests r ON r.id = a.request_id"
            " WHERE r.subject = ? GROUP BY a.status", (s,))}
        exams, sols = uploaded_nums(s, "exam"), set(uploaded_nums(s, "solution"))
        missing = [n for n in exams if n not in sols]
        lines += [
            f"<b>{e(subject_name(s))}</b>",
            f"👥 مقبولين: {reqs.get('approved', 0)} | معلّقين: {reqs.get('pending', 0)} | مرفوضين: {reqs.get('rejected', 0)}",
            f"📝 اختبارات مفتوحة عند الطلاب: {atts.get('exam_sent', 0)} | منتهية: {atts.get('done', 0)}"
            f" | بانتظار حلول: {atts.get('waiting', 0)}",
            f"📂 اختبارات مرفوعة: {len(exams)} | حلول مرفوعة: {len(sols)}",
        ]
        if missing:
            shown = ", ".join(map(str, missing[:30])) + (" ..." if len(missing) > 30 else "")
            lines.append(f"⚠️ اختبارات بلا ملف حلول: {shown}")
        lines.append("")
    await update.message.reply_text("\n".join(lines))
