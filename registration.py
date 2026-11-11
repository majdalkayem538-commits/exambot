"""محادثة تسجيل الطالب: الاسم والبطاقة برسالة واحدة ← اختيار الآنسة ← تأكيد.
إذا رقم البطاقة موجود بقائمة البطاقات المسموحة، الطالب بينقبل تلقائياً بالمدة الافتراضية."""

from __future__ import annotations

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from common import menu_router, start
from config import ADMIN_IDS, INFO_MAX_LEN, NAME_MAX_LEN, OWNER_ID, SUBJECTS, duration_label, subject_name, teacher_title
from database import (default_days, execute, get_request, is_allowed_card, now, phone_required, same_card_requests,
                      user_requests)
from keyboards import BTN_REGISTER, BTN_STATUS, MENU_FILTER, REGISTER_FILTER, main_keyboard, reg_buttons, teacher_buttons
from utils import e, safe_send, student_card

INFO, SUBJECT, CONFIRM, PHONE = range(4)  # حالات المحادثة
BTN_SHARE_PHONE = "📱 مشاركة رقمي"
BTN_CANCEL = "❌ إلغاء التسجيل"
INFO_EXAMPLE = "مجد غسان القيم\n12345"
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
DIACRITICS = re.compile("[ً-ْـ]")           # الحركات والتطويل
NAME_WORD = re.compile("[ء-غف-يa-zA-Z]{2,}")  # حروف عربية أو إنكليزية، حرفين عالأقل


def parse_student_info(text: str) -> tuple[str | None, str | None, str | None]:
    """بيفصل الاسم الثلاثي ورقم البطاقة من رسالة وحدة.
    بيقبل سطرين أو سطر واحد، وبيتجاهل كلمات متل "الاسم:" و"رقم البطاقة:".
    بيرجع (الاسم، رقم البطاقة، رسالة الخطأ بالفصحى)."""
    if len(text or "") > INFO_MAX_LEN:
        return None, None, "⚠️ الرسالة طويلة جدًا. يُرجى إرسال الاسم الثلاثي ورقم البطاقة فقط."
    text = DIACRITICS.sub("", (text or "").translate(ARABIC_DIGITS))
    lines = []
    for line in text.splitlines():
        line = line.split(":", 1)[1] if ":" in line else line  # شيل "الاسم:" إلخ
        line = " ".join(line.split())
        if line:
            lines.append(line)
    tokens = " ".join(lines).split(" ") if lines else []

    card_tokens = [t for t in tokens if re.search(r"\d", t)]
    name_tokens = [t for t in tokens if not re.search(r"\d", t)]

    if not card_tokens:
        return None, None, "⚠️ لم أجد رقم بطاقة الاشتراك. يُرجى كتابته في سطر مستقل أسفل الاسم."
    if len(card_tokens) > 1:
        return None, None, "⚠️ يُرجى كتابة رقم بطاقة الاشتراك دون مسافات بين الأرقام."
    card = card_tokens[0].strip("-#")
    if not re.fullmatch(r"[0-9A-Za-z\-]{1,30}", card):
        return None, None, "⚠️ يجب أن يتكوّن رقم البطاقة من أرقام (أو أحرف إنكليزية) دون مسافات."
    if len(name_tokens) < 3 or not all(NAME_WORD.fullmatch(w) for w in name_tokens):
        return None, None, "⚠️ يجب أن يكون الاسم ثلاثيًا (الاسم، اسم الأب، الكنية) ومكتوبًا بالأحرف فقط."
    name = " ".join(name_tokens)
    if len(name_tokens) > 6 or len(name) > NAME_MAX_LEN:
        return None, None, "⚠️ الاسم طويل جدًا. يُرجى كتابة الاسم الثلاثي فقط."
    return name, card, None


def open_request(user_id: int, subject: str):
    """طلب سابق لنفس المادة لسّا فعّال (مو مرفوض)."""
    for r in user_requests(user_id):
        if r["subject"] == subject and r["status"] != "rejected":
            return r
    return None


def saved_info(user_id: int):
    """آخر اسم ورقم بطاقة سجّل فيهم الطالب (من طلب مو مرفوض)."""
    for r in user_requests(user_id):
        if r["status"] != "rejected":
            return r
    return None


def saved_phone(user_id: int) -> str:
    """آخر رقم هاتف مؤكَّد شاركه الطالب بأي طلب سابق."""
    for r in user_requests(user_id):
        if r["phone"]:
            return r["phone"]
    return ""


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_SHARE_PHONE, request_contact=True)], [KeyboardButton(BTN_CANCEL)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


async def ask_phone(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> int:
    await context.bot.send_message(
        user_id,
        "📱 تشترط الآنسة في هذه المادة تأكيد رقم الهاتف.\n"
        f"اضغط زر «{BTN_SHARE_PHONE}» أدناه، وسيُرسل رقم حسابك في تلغرام تلقائيًا.",
        reply_markup=phone_keyboard(),
    )
    return PHONE


async def show_confirm(context: ContextTypes.DEFAULT_TYPE, user_id: int, reg: dict, query=None) -> int:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأكيد وإرسال", callback_data="confirm"),
        InlineKeyboardButton("✏️ تعديل", callback_data="restart"),
    ]])
    subject = reg["subject"]
    text = (
        "<b>راجع بياناتك:</b>\n"
        f"👤 {e(reg['full_name'])}\n"
        f"🪪 بطاقة الاشتراك: {e(reg['card_number'])}\n"
        + (f"📱 الهاتف: {e(reg['phone'])}\n" if reg.get("phone") else "")
        + f"👩‍🏫 {e(teacher_title(subject))} — {e(subject_name(subject))}"
    )
    if query is not None:
        try:
            await query.edit_message_text(text, reply_markup=kb)
            return CONFIRM
        except BadRequest:
            pass
    await context.bot.send_message(user_id, text, reply_markup=kb)
    return CONFIRM


def remaining_subjects(user_id: int) -> list[str]:
    return [s for s in SUBJECTS if not open_request(user_id, s)]


async def reg_begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if update.callback_query:
        await update.callback_query.answer()
    if user.id in ADMIN_IDS or (OWNER_ID and user.id == OWNER_ID):
        await context.bot.send_message(user.id, "هاد الحساب ما بيقدر يسجّل كطالب.")
        return ConversationHandler.END
    context.user_data.pop("await", None)

    saved = saved_info(user.id)
    if saved:  # مسجّل من قبل: ما في داعي يعيد كتابة بياناته
        left = remaining_subjects(user.id)
        if not left:
            await context.bot.send_message(
                user.id, f"✅ أنت مسجّل في جميع المواد المتاحة.\nيمكنك متابعة اشتراكاتك عبر «{BTN_STATUS}».",
                reply_markup=main_keyboard(user.id),
            )
            return ConversationHandler.END
        rows = [[InlineKeyboardButton(f"📨 التسجيل لدى الآنسة {SUBJECTS[s]['teacher']} — {SUBJECTS[s]['name']}",
                                      callback_data=f"quick:{s}")] for s in left]
        rows.append([InlineKeyboardButton("✏️ التسجيل ببيانات مختلفة", callback_data="newdata")])
        await context.bot.send_message(
            user.id,
            "بياناتك المسجّلة:\n"
            f"👤 {e(saved['full_name'])}\n🪪 {e(saved['card_number'])}\n\n"
            "اختر المادة، وسيُرسل طلبك بهذه البيانات مباشرةً:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return ConversationHandler.END
    return await ask_info(update, context)


async def reg_begin_fresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """«التسجيل ببيانات مختلفة»: الطريقة العادية (اسم + بطاقة)."""
    await update.callback_query.answer()
    if update.effective_user.id in ADMIN_IDS or (OWNER_ID and update.effective_user.id == OWNER_ID):
        return ConversationHandler.END
    return await ask_info(update, context)


async def ask_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    context.user_data["reg"] = {}
    context.user_data.pop("await", None)
    await context.bot.send_message(
        user.id,
        "✍️ أرسل <b>في رسالة واحدة</b>:\n"
        "1️⃣ اسمك الثلاثي\n"
        "2️⃣ رقم بطاقة الاشتراك في المعهد\n\n"
        f"مثال:\n<code>{INFO_EXAMPLE}</code>\n\n"
        "للإلغاء: /cancel",
    )
    return INFO


async def reg_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name, card, error = parse_student_info(update.message.text)
    if error:
        await update.message.reply_text(
            f"{error}\n\nأعد الإرسال على النحو الآتي:\n<code>{INFO_EXAMPLE}</code>"
        )
        return INFO
    context.user_data["reg"] = {"full_name": name, "card_number": card}
    await update.message.reply_text(
        f"👤 {e(name)}\n🪪 {e(card)}\n\n<b>اختر الآنسة:</b>", reply_markup=teacher_buttons()
    )
    return SUBJECT


async def reg_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":", 1)[1]
    reg = context.user_data.get("reg")
    if not reg or subject not in SUBJECTS:
        await query.edit_message_text(f"انتهت الجلسة. ابدأ من جديد عبر «{BTN_REGISTER}».")
        return ConversationHandler.END
    existing = open_request(update.effective_user.id, subject)
    if existing:
        await query.edit_message_text(
            f"⚠️ لديك طلب سابق لدى {e(teacher_title(subject))} (رقم {existing['id']}). "
            f"يمكنك متابعة حالته عبر «{BTN_STATUS}»."
        )
        context.user_data.pop("reg", None)
        return ConversationHandler.END
    reg["subject"] = subject
    if phone_required(subject) and not reg.get("phone"):
        reg["phone"] = saved_phone(update.effective_user.id)  # إذا شاركه من قبل، ما منطلبه مرة تانية
        if not reg["phone"]:
            await query.edit_message_text(f"👩‍🏫 {e(teacher_title(subject))} — {e(subject_name(subject))}")
            return await ask_phone(context, update.effective_user.id)
    return await show_confirm(context, update.effective_user.id, reg, query)


async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """الطالب شارك رقمه بزر «مشاركة رقمي». منقبل بس رقم حسابه هو (مو جهة اتصال لحدا تاني)."""
    msg, user = update.message, update.effective_user
    reg = context.user_data.get("reg")
    if not reg or reg.get("subject") not in SUBJECTS:
        await msg.reply_text(f"انتهت الجلسة. ابدأ من جديد عبر «{BTN_REGISTER}».", reply_markup=main_keyboard(user.id))
        return ConversationHandler.END
    if msg.text == BTN_CANCEL:
        return await reg_cancel(update, context)
    if not msg.contact:
        await msg.reply_text(f"يُرجى الضغط على زر «{BTN_SHARE_PHONE}» أدناه (لا تكتب الرقم يدويًا).",
                             reply_markup=phone_keyboard())
        return PHONE
    if msg.contact.user_id != user.id:
        await msg.reply_text("⚠️ يجب مشاركة رقم حسابك أنت، لا رقم شخص آخر.", reply_markup=phone_keyboard())
        return PHONE
    digits = "".join(ch for ch in msg.contact.phone_number if ch.isdigit())
    reg["phone"] = "+" + digits
    await msg.reply_text("✅ تم تأكيد رقم هاتفك.", reply_markup=main_keyboard(user.id))
    return await show_confirm(context, user.id, reg)


async def reg_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    if query.data == "restart":
        await query.edit_message_text("حسنًا، لنبدأ من جديد.")
        return await reg_begin(update, context)  # reg_begin بيعمل answer

    await query.answer()
    reg = context.user_data.pop("reg", None)
    if not reg or reg.get("subject") not in SUBJECTS:
        await query.edit_message_text(f"انتهت الجلسة. ابدأ من جديد عبر «{BTN_REGISTER}».")
        return ConversationHandler.END
    subject = reg["subject"]
    existing = open_request(user.id, subject)  # فحص تاني قبل الحفظ
    if existing:
        await query.edit_message_text(f"⚠️ لديك طلب سابق (رقم {existing['id']}). تابع حالته عبر «{BTN_STATUS}».")
        return ConversationHandler.END

    if phone_required(subject) and not reg.get("phone"):  # الإعداد انفعّل بنص التسجيل
        context.user_data["reg"] = reg
        await query.edit_message_text("📱 أصبح تأكيد رقم الهاتف مطلوبًا في هذه المادة.")
        return await ask_phone(context, user.id)
    await query.edit_message_text(
        await submit_request(context, user, subject, reg["full_name"], reg["card_number"], reg.get("phone", "")))
    return ConversationHandler.END


async def submit_request(context: ContextTypes.DEFAULT_TYPE, user, subject: str, name: str, card: str,
                         phone: str = "") -> str:
    """بيسجّل الطلب: قبول تلقائي إذا البطاقة مسموحة، وإلا بيبعته للآنسة. بيرجّع الرد للطالب."""
    req_id = execute(
        "INSERT INTO requests(user_id, username, full_name, phone, subject, card_number, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (user.id, user.username, name, phone, subject, card, now(), now()),
    )
    req = get_request(req_id)
    admin = SUBJECTS[subject]["admin"]

    # قبول تلقائي: البطاقة بالقائمة، ومو مستخدمة من حساب تاني
    if is_allowed_card(subject, card) and not same_card_requests(subject, card, user.id):
        from subscription import do_approve
        days = default_days(subject)
        if await do_approve(context, req, days):
            await safe_send(
                context.bot, admin,
                student_card(get_request(req_id), f"✅ قبول تلقائي #{req_id}")
                + f"\n📅 المدة: {duration_label(days)}\n(البطاقة موجودة بقائمة البطاقات المسموحة)",
            )
            return f"✅ تم التحقق من رقم بطاقتك، وقُبل طلبك رقم <b>{req_id}</b> في مادة {e(subject_name(subject))} مباشرةً."

    await safe_send(
        context.bot, admin,
        student_card(req, f"📥 طلب تسجيل #{req_id}", warn_shared_card=True), reply_markup=reg_buttons(req_id),
    )
    return (f"✅ أُرسل طلبك برقم <b>{req_id}</b> إلى {e(teacher_title(subject))} "
            f"(مادة {e(subject_name(subject))}).\nسيصلك إشعار هنا فور الموافقة عليه.")


async def quick_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """quick:<subject> — تسجيل بمادة جديدة بالبيانات المحفوظة، بكبسة وحدة (+ الهاتف إذا مطلوب وما في رقم محفوظ)."""
    query = update.callback_query
    user = update.effective_user
    subject = query.data.split(":", 1)[1]
    saved = saved_info(user.id)
    if user.id in ADMIN_IDS or (OWNER_ID and user.id == OWNER_ID) or subject not in SUBJECTS or not saved:
        await query.answer("غير صالح.", show_alert=True)
        return ConversationHandler.END
    existing = open_request(user.id, subject)
    if existing:
        await query.answer(f"لديك طلب سابق في هذه المادة (رقم {existing['id']}).", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    phone = saved_phone(user.id)
    if phone_required(subject) and not phone:
        context.user_data["reg"] = {"full_name": saved["full_name"], "card_number": saved["card_number"],
                                    "subject": subject}
        return await ask_phone(context, user.id)
    text = await submit_request(context, user, subject, saved["full_name"], saved["card_number"], phone)
    left = remaining_subjects(user.id)
    if left:
        rows = [[InlineKeyboardButton(f"📨 التسجيل لدى الآنسة {SUBJECTS[s]['teacher']} — {SUBJECTS[s]['name']}",
                                      callback_data=f"quick:{s}")] for s in left]
        kb = InlineKeyboardMarkup(rows)
    else:
        kb = None
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(user.id, text, reply_markup=kb)
    return ConversationHandler.END


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("reg", None)  # بيرجّع الأزرار الأساسية بدل زر مشاركة الرقم
    await update.message.reply_text("تم الإلغاء.", reply_markup=main_keyboard(update.effective_user.id))
    return ConversationHandler.END


async def reg_restart_from_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("reg", None)
    await start(update, context)
    return ConversationHandler.END


async def menu_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إذا ضغط زر من القائمة بنص التسجيل: بنلغي التسجيل وبننفّذ الزر."""
    context.user_data.pop("reg", None)
    if REGISTER_FILTER.check_update(update):
        return await reg_begin(update, context)
    await menu_router(update, context)
    return ConversationHandler.END


def build_registration_handler() -> ConversationHandler:
    private = filters.ChatType.PRIVATE
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(reg_begin, pattern=r"^new$"),
            CallbackQueryHandler(reg_begin_fresh, pattern=r"^newdata$"),
            CallbackQueryHandler(quick_register, pattern=r"^quick:s\d$"),
            CommandHandler("register", reg_begin, filters=private),
            MessageHandler(private & REGISTER_FILTER, reg_begin),
        ],
        states={
            INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_FILTER, reg_info)],
            SUBJECT: [CallbackQueryHandler(reg_subject, pattern=r"^subj:s\d$")],
            CONFIRM: [CallbackQueryHandler(reg_confirm, pattern=r"^(confirm|restart)$")],
            PHONE: [MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, reg_phone)],
        },
        fallbacks=[
            CommandHandler("cancel", reg_cancel),
            CommandHandler("start", reg_restart_from_start),
            MessageHandler(MENU_FILTER, menu_exit),
        ],
        allow_reentry=True,
    )
