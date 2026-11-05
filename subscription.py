"""الاشتراكات: الآنسة بتحدد المدة وقت القبول، وبتقدر تمدّد. البوت بيذكّر الطالب قبل الانتهاء
وبيوقف وصوله تلقائياً لما يخلص، والطالب بيقدر يطلب تجديد بكبسة زر."""

from __future__ import annotations

import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import (REMIND_DAYS, RENEW_COOLDOWN_HOURS, REQ_LABELS, SUBJECTS, can_manage, duration_label, log,
                    subject_name, teacher_title)
from database import (after_days, approve_request, days_left, execute, get_request, minutes_since, now, q_all,
                      set_request_status)
from keyboards import BTN_EXAMS, duration_picker, main_keyboard, renew_button
from utils import close_admin_card, e, safe_send, student_card

MAX_CUSTOM_DAYS = 3650


def expiry_text(req) -> str:
    """وصف الاشتراك للآنسة."""
    if not req["expires_at"]:
        return "♾️ بدون انتهاء"
    left = days_left(req["expires_at"])
    return f"لحد {req['expires_at'][:10]} (باقي {left} يوم)" if left else f"انتهى {req['expires_at'][:10]}"


def days_ar(n: int) -> str:
    """العدد مع التمييز بالفصحى: يوم واحد، يومان، 3 أيام، 11 يومًا."""
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومان"
    if 3 <= n % 100 <= 10:
        return f"{n} أيام"
    return f"{n} يومًا"


def expiry_text_student(req) -> str:
    """وصف الاشتراك للطالب (فصحى)."""
    if not req["expires_at"]:
        return "اشتراكك غير محدد المدة."
    left = days_left(req["expires_at"])
    if req["status"] == "expired" or not left:
        return f"انتهى اشتراكك بتاريخ {req['expires_at'][:10]}."
    return f"ينتهي اشتراكك بتاريخ {req['expires_at'][:10]} (المتبقي: {days_ar(left)})."


# ---------------------------------------------------------------- قبول الطلب مع المدة
async def show_approve_picker(query, req) -> None:
    kb = duration_picker("ap", str(req["id"]), req["subject"], back=f"apb:{req['id']}")
    try:
        await query.edit_message_reply_markup(kb)
    except BadRequest:
        pass


async def approve_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    req = get_request(int(query.data.split(":")[1]))
    if not req or not can_manage(query.from_user.id, req["subject"]):
        await query.answer("مو مسموح.", show_alert=True)
        return
    await query.answer()
    from keyboards import reg_buttons
    try:
        await query.edit_message_reply_markup(reg_buttons(req["id"]))
    except BadRequest:
        pass


async def do_approve(context: ContextTypes.DEFAULT_TYPE, req, days: int) -> bool:
    """بيقبل الطلب وبيخبّر الطالب. بيرجّع False إذا الطلب ما عاد معلّق."""
    if not approve_request(req["id"], days, only_from=("pending",)):
        return False
    req = get_request(req["id"])
    await safe_send(
        context.bot, req["user_id"],
        f"🎉 تم قبول طلبك رقم {req['id']} في مادة <b>{e(subject_name(req['subject']))}</b>.\n"
        f"{expiry_text_student(req)}\n"
        f"اضغط «{BTN_EXAMS}» لعرض الاختبارات.",
        reply_markup=main_keyboard(req["user_id"]),
    )
    return True


async def approve_with_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, req_id, days = query.data.split(":")
    req, days = get_request(int(req_id)), int(days)
    if not req or not can_manage(query.from_user.id, req["subject"]) or days > MAX_CUSTOM_DAYS:
        await query.answer("مو مسموح.", show_alert=True)
        return
    if not await do_approve(context, req, days):
        await query.answer("تم التعامل مع هاد الطلب من قبل.", show_alert=True)
        current = get_request(req["id"])
        await close_admin_card(query, f"ℹ️ الحالة: {REQ_LABELS.get(current['status'], current['status'])}")
        return
    await query.answer()
    await close_admin_card(query, f"✅ <b>تم القبول</b> — المدة: {duration_label(days)}")


async def custom_days_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """apc:<req> / extc:<req> / sddc:<subject> — الآنسة بتكتب عدد الأيام."""
    query = update.callback_query
    kind, target = query.data.split(":")
    subject = target if kind == "sddc" else (get_request(int(target)) or {"subject": None})["subject"]
    if not subject or not can_manage(query.from_user.id, subject):
        await query.answer("مو مسموح.", show_alert=True)
        return
    await query.answer()
    context.user_data["await"] = {"kind": "days", "action": kind[:-1], "target": target, "t": time.time()}
    await context.bot.send_message(query.from_user.id, f"اكتبي عدد الأيام (من 1 لـ {MAX_CUSTOM_DAYS}):")


async def custom_days_received(update: Update, context: ContextTypes.DEFAULT_TYPE, wait: dict) -> None:
    text = (update.message.text or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not text.isdigit() or not 1 <= int(text) <= MAX_CUSTOM_DAYS:
        await update.message.reply_text(f"⚠️ اكتبي رقم بس، من 1 لـ {MAX_CUSTOM_DAYS}. أو اضغطي أي زر من تحت للإلغاء.")
        context.user_data["await"] = wait
        return
    days, uid, action = int(text), update.effective_user.id, wait["action"]
    if action == "sdd":
        from settings import set_default_days
        await set_default_days(update, context, wait["target"], days)
        return
    req = get_request(int(wait["target"]))
    if not req or not can_manage(uid, req["subject"]):
        return
    if action == "ap":
        ok = await do_approve(context, req, days)
        await update.message.reply_text(
            f"✅ تم قبول {e(req['full_name'])} لمدة {days} يوم." if ok else "تم التعامل مع هاد الطلب من قبل."
        )
    else:  # ext
        await do_extend(context, req, days)
        await update.message.reply_text(f"✅ تم تمديد اشتراك {e(req['full_name'])}. {expiry_text(get_request(req['id']))}")


# ---------------------------------------------------------------- التمديد والتجديد
async def do_extend(context: ContextTypes.DEFAULT_TYPE, req, days: int) -> bool:
    if not approve_request(req["id"], days, only_from=("approved", "expired")):
        return False
    req = get_request(req["id"])
    await safe_send(
        context.bot, req["user_id"],
        f"✅ تم تجديد اشتراكك في مادة <b>{e(subject_name(req['subject']))}</b>.\n{expiry_text_student(req)}",
        reply_markup=main_keyboard(req["user_id"]),
    )
    return True


async def extend_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """extm:<req>:<page> — من تفاصيل الطالب، أو من طلب التجديد (page = r)."""
    query = update.callback_query
    _, req_id, page = query.data.split(":")
    req = get_request(int(req_id))
    if not req or not can_manage(query.from_user.id, req["subject"]):
        await query.answer("مو مسموح.", show_alert=True)
        return
    await query.answer()
    back = f"std:{req['id']}:{page}" if page.isdigit() else None
    kb = duration_picker("ext", str(req["id"]), req["subject"], back=back)
    text = (f"⏳ تمديد اشتراك <b>{e(req['full_name'])}</b>\nالاشتراك الحالي: {expiry_text(req)}\n\n"
            "اختاري المدة (بتنضاف على آخر الاشتراك إذا لسّا ما خلص):")
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(query.from_user.id, text, reply_markup=kb)


async def extend_with_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, req_id, days = query.data.split(":")
    req, days = get_request(int(req_id)), int(days)
    if not req or not can_manage(query.from_user.id, req["subject"]) or days > MAX_CUSTOM_DAYS:
        await query.answer("مو مسموح.", show_alert=True)
        return
    if not await do_extend(context, req, days):
        await query.answer("ما بينفع التمديد بهالحالة (الطالب مرفوض أو موقوف).", show_alert=True)
        return
    await query.answer("✅ تم التمديد", show_alert=False)
    req = get_request(req["id"])
    try:
        await query.edit_message_text(f"✅ تم تمديد اشتراك <b>{e(req['full_name'])}</b>\n{expiry_text(req)}")
    except BadRequest:
        pass


async def renew_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """الطالب بيطلب تجديد: بتوصل للآنسة بطاقة فيها أزرار المدة."""
    query = update.callback_query
    req = get_request(int(query.data.split(":")[1]))
    if not req or req["user_id"] != query.from_user.id or req["status"] not in ("approved", "expired"):
        await query.answer("الطلب غير صالح.", show_alert=True)
        return
    last = req["renew_requested_at"]
    if last and minutes_since(last) < RENEW_COOLDOWN_HOURS * 60:
        await query.answer("لقد أرسلت طلب تجديد مؤخرًا، يُرجى انتظار ردّ الآنسة.", show_alert=True)
        return
    execute("UPDATE requests SET renew_requested_at = ? WHERE id = ?", (now(), req["id"]))
    await query.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏳ تجديد", callback_data=f"extm:{req['id']}:r")]])
    sent = await safe_send(
        context.bot, SUBJECTS[req["subject"]]["admin"],
        student_card(req, f"🔄 طلب تجديد اشتراك #{req['id']}") + f"\n📅 الاشتراك: {expiry_text(req)}",
        reply_markup=kb,
    )
    await context.bot.send_message(
        query.from_user.id,
        f"✅ أُرسل طلب التجديد إلى {e(teacher_title(req['subject']))}، وسيصلك إشعار عند الموافقة."
        if sent else "تعذّر إرسال الطلب حاليًا، يُرجى المحاولة لاحقًا.",
    )


# ---------------------------------------------------------------- مهمة دورية: التذكير والإيقاف التلقائي
async def subscription_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    stamp = now()
    # 1) انتهت: بيتوقف الوصول وبيوصل للطالب زر طلب تجديد
    expired = q_all(
        "SELECT * FROM requests WHERE status = 'approved' AND expires_at IS NOT NULL AND expires_at <= ?", (stamp,)
    )
    per_subject: dict[str, list[str]] = {}
    for req in expired:
        if not set_request_status(req["id"], "expired", only_from=("approved",)):
            continue
        per_subject.setdefault(req["subject"], []).append(req["full_name"])
        await safe_send(
            context.bot, req["user_id"],
            f"⌛ انتهى اشتراكك في مادة <b>{e(subject_name(req['subject']))}</b>.\n"
            "لتجديد الاشتراك اضغط الزر أدناه، وسيُرسل طلبك إلى الآنسة.",
            reply_markup=renew_button(req["id"]),
        )
    for subject, names in per_subject.items():
        shown = "، ".join(e(n) for n in names[:20]) + (" ..." if len(names) > 20 else "")
        await safe_send(
            context.bot, SUBJECTS[subject]["admin"],
            f"⌛ انتهى اشتراك {len(names)} طالب بمادة {e(subject_name(subject))}:\n{shown}\n\n"
            "فيكي تمدّدي لأي حدا منهم من 📊 إحصائيات ← قائمة الطلاب.",
        )

    # 2) قرّب ينتهي: تذكير مرة وحدة
    soon = q_all(
        "SELECT * FROM requests WHERE status = 'approved' AND reminded = 0 AND expires_at IS NOT NULL"
        " AND expires_at > ? AND expires_at <= ?",
        (stamp, after_days(REMIND_DAYS)),
    )
    for req in soon:
        execute("UPDATE requests SET reminded = 1 WHERE id = ?", (req["id"],))
        await safe_send(
            context.bot, req["user_id"],
            f"🔔 تذكير: {expiry_text_student(req)}\nالمادة: {e(subject_name(req['subject']))}.\n"
            "لتجديد الاشتراك اضغط الزر أدناه.",
            reply_markup=renew_button(req["id"]),
        )
    if expired or soon:
        log.info("subscriptions: %d expired, %d reminded", len(expired), len(soon))
