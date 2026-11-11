"""إعدادات الآنسة لكل مادة: مدة الاشتراك الافتراضية، قائمة البطاقات للقبول التلقائي، إشعار الاختبارات الجديدة."""

from __future__ import annotations

import io
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import ADMIN_SUBJECTS, OWNER_ID, SUBJECTS, can_manage, duration_label, log, subject_name, teacher_title
from database import (add_allowed_cards, allowed_cards, clear_allowed_cards, default_days, notify_new_exams,
                      phone_required, set_setting)
from keyboards import duration_picker, subject_chooser
from utils import e, safe_send

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
MAX_CARDS_FILE = 1024 * 1024  # 1MB


def settings_screen(subject: str) -> tuple[str, InlineKeyboardMarkup]:
    d = default_days(subject)
    n_cards = len(allowed_cards(subject))
    notify = notify_new_exams(subject)
    phone = phone_required(subject)
    text = (
        f"<b>⚙️ إعدادات {e(subject_name(subject))}</b>\n\n"
        f"⏳ <b>مدة الاشتراك الافتراضية:</b> {duration_label(d)}\n"
        "بتطلع أول خيار وقت القبول، وبتنستخدم بالقبول التلقائي.\n\n"
        f"📋 <b>البطاقات المسموحة (قبول تلقائي):</b> {n_cards} بطاقة\n"
        "الطالب يلي رقم بطاقته بالقائمة بينقبل لحاله بالمدة الافتراضية.\n\n"
        f"🔔 <b>إشعار الطلاب بالاختبارات الجديدة:</b> {'مفعّل ✅' if notify else 'معطّل ❌'}\n\n"
        f"📱 <b>طلب رقم الهاتف عند التسجيل:</b> {'مفعّل ✅' if phone else 'معطّل ❌'}\n"
        "الطالب بيشارك رقمه بزر من تلغرام (رقم حسابه الحقيقي، ما بيقدر يكتب رقم وهمي)."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ تغيير المدة الافتراضية", callback_data=f"sdm:{subject}")],
        [InlineKeyboardButton("📋 البطاقات المسموحة", callback_data=f"crd:{subject}")],
        [InlineKeyboardButton("🔕 إيقاف الإشعارات" if notify else "🔔 تفعيل الإشعارات", callback_data=f"ntf:{subject}")],
        [InlineKeyboardButton("📱 إيقاف طلب الهاتف" if phone else "📱 تفعيل طلب الهاتف", callback_data=f"phn:{subject}")],
    ])
    return text, kb


def cards_screen(subject: str) -> tuple[str, InlineKeyboardMarkup]:
    cards = allowed_cards(subject)
    preview = "، ".join(cards[:15]) + (" ..." if len(cards) > 15 else "")
    text = (
        f"<b>📋 البطاقات المسموحة — {e(subject_name(subject))}</b>\n"
        f"العدد: <b>{len(cards)}</b>\n" + (f"<code>{e(preview)}</code>\n" if cards else "") +
        "\nإذا سجّل طالب برقم موجود هون، بينقبل تلقائياً (إلا إذا نفس الرقم مستخدم من حساب تاني، "
        "وقتها بيوصلك الطلب لتقرري)."
    )
    rows = [[InlineKeyboardButton("➕ إضافة بطاقات", callback_data=f"cra:{subject}")]]
    if cards:
        rows.append([InlineKeyboardButton("📄 تنزيل القائمة", callback_data=f"crx:{subject}"),
                     InlineKeyboardButton("🗑 حذف الكل", callback_data=f"crc:{subject}")])
    rows.append([InlineKeyboardButton("↩️ الإعدادات", callback_data=f"cfgs:{subject}")])
    return text, InlineKeyboardMarkup(rows)


async def _show(query, text: str, kb) -> None:
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest:
        pass


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    subjects = list(SUBJECTS) if (OWNER_ID and uid == OWNER_ID) else ADMIN_SUBJECTS.get(uid, [])
    if not subjects:
        return
    if len(subjects) == 1:
        text, kb = settings_screen(subjects[0])
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text("لأي مادة؟", reply_markup=subject_chooser("cfgs", subjects))


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    kind, subject, *rest = query.data.split(":")
    if not can_manage(query.from_user.id, subject):
        await query.answer("مو مسموح.", show_alert=True)
        return

    if kind == "cfgs":
        await query.answer()
        await _show(query, *settings_screen(subject))
    elif kind == "sdm":
        await query.answer()
        await _show(query, "اختاري مدة الاشتراك الافتراضية:",
                    duration_picker("sdd", subject, subject, back=f"cfgs:{subject}"))
    elif kind == "sdd":
        set_setting(subject, "default_days", str(int(rest[0])))
        await query.answer(f"✅ المدة الافتراضية: {duration_label(int(rest[0]))}")
        await _show(query, *settings_screen(subject))
    elif kind == "phn":
        on = not phone_required(subject)
        set_setting(subject, "require_phone", "1" if on else "0")
        await query.answer(f"📱 طلب الهاتف صار {'مفعّل' if on else 'معطّل'}")
        await _show(query, *settings_screen(subject))
        log.info("require_phone for %s set to %s by %s", subject, on, query.from_user.id)
        admin = SUBJECTS[subject]["admin"]
        if query.from_user.id != admin:  # صاحب البوت غيّرها: منخبّر الآنسة
            await safe_send(context.bot, admin,
                            f"ℹ️ صاحب البوت {'فعّل' if on else 'وقّف'} طلب رقم الهاتف عند التسجيل "
                            f"بمادة {e(subject_name(subject))} ({e(teacher_title(subject))}).")
    elif kind == "ntf":
        set_setting(subject, "notify_new", "0" if notify_new_exams(subject) else "1")
        await query.answer("✅ تم")
        await _show(query, *settings_screen(subject))
    elif kind == "crd":
        await query.answer()
        await _show(query, *cards_screen(subject))
    elif kind == "cra":
        await query.answer()
        context.user_data["await"] = {"kind": "cards", "subject": subject, "t": time.time()}
        await context.bot.send_message(
            query.from_user.id,
            "ابعتيلي أرقام البطاقات المسموحة: كرسالة (كل رقم بسطر أو بينهم فراغ أو فاصلة)، "
            "أو كملف txt / csv.\nالأرقام الجديدة بتنضاف للقائمة الموجودة.\n\nللإلغاء اضغطي أي زر من تحت.",
        )
    elif kind == "crx":
        await query.answer()
        data = "\n".join(allowed_cards(subject)).encode("utf-8")
        await context.bot.send_document(
            query.from_user.id, io.BytesIO(data), filename=f"cards_{subject}.txt",
            caption=f"📋 البطاقات المسموحة — {e(subject_name(subject))}", protect_content=False,
        )
    elif kind == "crc":
        await query.answer()
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 نعم، احذفي الكل", callback_data=f"crcy:{subject}"),
            InlineKeyboardButton("↩️ لا", callback_data=f"crd:{subject}"),
        ]])
        await _show(query, "متأكدة بدك تحذفي كل البطاقات المسموحة؟\n(الطلاب المقبولين ما بيتأثروا)", kb)
    elif kind == "crcy":
        n = clear_allowed_cards(subject)
        await query.answer(f"🗑 انحذفت {n} بطاقة", show_alert=True)
        await _show(query, *cards_screen(subject))


def parse_cards(text: str) -> list[str]:
    text = (text or "").translate(ARABIC_DIGITS)
    tokens = re.split(r"[\s,;،]+", text)
    return sorted({t.strip("-#") for t in tokens
                   if t and re.fullmatch(r"[0-9A-Za-z\-#]{1,30}", t) and re.search(r"\d", t)})


async def cards_received(update: Update, context: ContextTypes.DEFAULT_TYPE, wait: dict) -> None:
    msg = update.message
    subject = wait["subject"]
    if msg.document:
        if msg.document.file_size and msg.document.file_size > MAX_CARDS_FILE:
            await msg.reply_text("⚠️ الملف كبير كتير (أكتر من 1MB).")
            return
        raw = bytes(await (await msg.document.get_file()).download_as_bytearray())
        text = raw.decode("utf-8-sig", errors="ignore")
    else:
        text = msg.text or ""
    cards = parse_cards(text)
    if not cards:
        await msg.reply_text("⚠️ ما لقيت أرقام بطاقات. جرّبي مرة تانية، أو اضغطي أي زر من تحت للإلغاء.")
        context.user_data["await"] = wait
        return
    added = add_allowed_cards(subject, cards)
    text, kb = cards_screen(subject)
    await msg.reply_text(f"✅ انضاف {added} رقم جديد ({len(cards) - added} كانوا موجودين).\n\n" + text, reply_markup=kb)


async def set_default_days(update: Update, context: ContextTypes.DEFAULT_TYPE, subject: str, days: int) -> None:
    set_setting(subject, "default_days", str(days))
    text, kb = settings_screen(subject)
    await update.message.reply_text(f"✅ المدة الافتراضية صارت {duration_label(days)}.\n\n" + text, reply_markup=kb)
