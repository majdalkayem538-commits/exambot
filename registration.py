from __future__ import annotations

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from common import menu_router, start
from config import ADMIN_IDS, SUBJECTS, subject_name, teacher_title
from database import execute, get_request, now, user_requests
from keyboards import BTN_REGISTER, BTN_STATUS, MENU_FILTER, main_keyboard, reg_buttons, teacher_buttons
from utils import e, safe_send, student_card

INFO, SUBJECT, CONFIRM = range(3)  # حالات المحادثة
INFO_EXAMPLE = "مجد غسان القيم\n12345"
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def parse_student_info(text: str) -> tuple[str | None, str | None, str | None]:
    """بيفصل الاسم الثلاثي ورقم البطاقة من رسالة وحدة.
    بيقبل سطرين أو سطر واحد، وبيتجاهل كلمات متل "الاسم:" و"رقم البطاقة:".
    بيرجع (الاسم، رقم البطاقة، رسالة الخطأ)."""
    text = (text or "").translate(ARABIC_DIGITS)
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
        return None, None, "⚠️ ما قدرت لاقي رقم بطاقة الاشتراك. اكتبه بسطر لحاله تحت الاسم."
    if len(card_tokens) > 1:
        return None, None, "⚠️ اكتب رقم بطاقة الاشتراك بدون فراغات بين الأرقام."
    card = card_tokens[0].strip("-#")
    if not re.fullmatch(r"[0-9A-Za-z\-]{1,30}", card):
        return None, None, "⚠️ رقم البطاقة لازم يكون أرقام (أو أحرف إنكليزية) بدون فراغات."
    if len(name_tokens) < 3 or not all(re.fullmatch(r"[\u0621-\u064Aa-zA-Z]+", w) for w in name_tokens):
        return None, None, "⚠️ لازم الاسم يكون ثلاثي (الاسم، اسم الأب، الكنية) وبالأحرف فقط."
    return " ".join(name_tokens), card, None


async def reg_begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if update.callback_query:
        await update.callback_query.answer()
    if user.id in ADMIN_IDS:
        await context.bot.send_message(user.id, "حساب الادمن ما بيقدر يسجّل كطالب.")
        return ConversationHandler.END
    context.user_data["reg"] = {}
    await context.bot.send_message(
        user.id,
        "✍️ ابعت <b>برسالة وحدة</b>:\n"
        "1️⃣ اسمك الثلاثي\n"
        "2️⃣ رقم بطاقة الاشتراك بالمعهد\n\n"
        f"مثال:\n<code>{INFO_EXAMPLE}</code>\n\n"
        "للإلغاء: /cancel",
    )
    return INFO


async def reg_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name, card, error = parse_student_info(update.message.text)
    if error:
        await update.message.reply_text(
            f"{error}\n\nابعتهم مرة تانية متل هالمثال:\n<code>{INFO_EXAMPLE}</code>"
        )
        return INFO
    context.user_data["reg"] = {"full_name": name, "card_number": card}
    await update.message.reply_text(
        f"👤 {e(name)}\n🪪 {e(card)}\n\n<b>اختار الآنسة:</b>", reply_markup=teacher_buttons()
    )
    return SUBJECT


async def reg_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":", 1)[1]
    reg = context.user_data.get("reg")
    if not reg or subject not in SUBJECTS:
        await query.edit_message_text(f"انتهت الجلسة. ابدأ من جديد من «{BTN_REGISTER}».")
        return ConversationHandler.END
    existing = [
        r for r in user_requests(update.effective_user.id)
        if r["subject"] == subject and r["status"] != "rejected"
    ]
    if existing:
        await query.edit_message_text(
            f"⚠️ عندك طلب عند {e(teacher_title(subject))} من قبل (#{existing[0]['id']}). "
            f"شوف حالته من «{BTN_STATUS}»."
        )
        context.user_data.pop("reg", None)
        return ConversationHandler.END
    reg["subject"] = subject
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأكيد وإرسال", callback_data="confirm"),
        InlineKeyboardButton("✏️ إعادة", callback_data="restart"),
    ]])
    await query.edit_message_text(
        "<b>راجع معلوماتك:</b>\n"
        f"👤 {e(reg['full_name'])}\n"
        f"🪪 بطاقة الاشتراك: {e(reg['card_number'])}\n"
        f"👩‍🏫 {e(teacher_title(subject))} — {e(subject_name(subject))}",
        reply_markup=kb,
    )
    return CONFIRM


async def reg_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    if query.data == "restart":
        await query.edit_message_text("تمام، منبلّش من جديد.")
        return await reg_begin(update, context)  # reg_begin بيعمل answer

    await query.answer()
    reg = context.user_data.pop("reg", None)
    if not reg:
        await query.edit_message_text(f"انتهت الجلسة. ابدأ من جديد من «{BTN_REGISTER}».")
        return ConversationHandler.END

    req_id = execute(
        "INSERT INTO requests(user_id, username, full_name, phone, subject, card_number, status, created_at, updated_at)"
        " VALUES (?, ?, ?, '', ?, ?, 'pending', ?, ?)",
        (user.id, user.username, reg["full_name"], reg["subject"], reg["card_number"], now(), now()),
    )
    req = get_request(req_id)
    await query.edit_message_text(
        f"✅ انبعت طلبك برقم <b>#{req_id}</b> لـ{e(teacher_title(req['subject']))} "
        f"(مادة {e(subject_name(req['subject']))}).\nرح يوصلك إشعار هون أول ما توافق."
    )
    await safe_send(
        context.bot, SUBJECTS[req["subject"]]["admin"],
        student_card(req, f"📥 طلب تسجيل #{req_id}"), reply_markup=reg_buttons(req_id),
    )
    return ConversationHandler.END


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("reg", None)
    await update.message.reply_text("تم الإلغاء.", reply_markup=main_keyboard(update.effective_user.id))
    return ConversationHandler.END


async def reg_restart_from_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("reg", None)
    await start(update, context)
    return ConversationHandler.END


async def menu_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إذا ضغط زر من القائمة بنص التسجيل: بنلغي التسجيل وبننفّذ الزر."""
    context.user_data.pop("reg", None)
    if update.message.text == BTN_REGISTER:
        return await reg_begin(update, context)
    await menu_router(update, context)
    return ConversationHandler.END


def build_registration_handler() -> ConversationHandler:
    private = filters.ChatType.PRIVATE
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(reg_begin, pattern=r"^new$"),
            CommandHandler("register", reg_begin, filters=private),
            MessageHandler(private & filters.Text([BTN_REGISTER]), reg_begin),
        ],
        states={
            INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_FILTER, reg_info)],
            SUBJECT: [CallbackQueryHandler(reg_subject, pattern=r"^subj:")],
            CONFIRM: [CallbackQueryHandler(reg_confirm, pattern=r"^(confirm|restart)$")],
        },
        fallbacks=[
            CommandHandler("cancel", reg_cancel),
            CommandHandler("start", reg_restart_from_start),
            MessageHandler(MENU_FILTER, menu_exit),
        ],
        allow_reentry=True,
    )
