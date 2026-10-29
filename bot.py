"""
بوت اختبارات تلغرام — مادتين، لكل مادة آنسة (أدمن)، وعدد غير محدود من الاختبارات، لكل اختبار ملف حلول.

مسار الطالب:
  تسجيل (اسم ثلاثي + رقم بطاقة الاشتراك برسالة وحدة) -> بيختار الآنسة -> طلب برقم بيروح للآنسة
  -> موافقة -> بيختار اختبار وبيستلم ملفه -> بيضغط «لقد أنهيت الحل»
  -> البوت بيبعتلو ملف الحلول تلقائياً.

كل شي بيبعتو البوت محمي (protect_content): ممنوع التحويل والحفظ ونسخ النص،
ولقطة الشاشة ممنوعة على أندرويد.
"""

from __future__ import annotations

# ================================================================
#   ⚙️  الإعدادات — عدّل هون بس
#   (على Render ما في داعي تعدّل هون: القيم بتنحط من لوحة Render)
# ================================================================
BOT_TOKEN = "حط_توكن_البوت_هون"          # من @BotFather

# الـ ID الرقمي لكل آنسة (بتجيبه من @userinfobot)
SAMARA_ID = 111111111                    # الآنسة سمارا — التحليل الكلي
ZAINAB_ID = 222222222                    # الآنسة زينب — الرياضيات الاقتصادية

DB_PATH = "exam_bot.db"

SUBJECT_CONFIG = {
    "s1": {"name": "التحليل الكلي", "teacher": "سمارا", "admin": SAMARA_ID},
    "s2": {"name": "الرياضيات الاقتصادية", "teacher": "زينب", "admin": ZAINAB_ID},
}
# ================================================================

import subprocess
import sys

# تنزيل المكتبة تلقائياً إذا مو موجودة
try:
    import telegram  # noqa: F401
except ImportError:
    print("Installing python-telegram-bot ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot==21.*"])

import html
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    Defaults,
    MessageHandler,
    filters,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("exam_bot")

# متغيرات البيئة (متل على Render) بتغلب على القيم فوق
BOT_TOKEN = os.getenv("BOT_TOKEN", BOT_TOKEN)
DB_PATH = os.getenv("DB_PATH", DB_PATH)

SUBJECTS: dict[str, dict] = {}
for i, (key, cfg) in enumerate(SUBJECT_CONFIG.items(), start=1):
    SUBJECTS[key] = {
        "name": os.getenv(f"SUBJECT_{i}_NAME", cfg["name"]),
        "teacher": os.getenv(f"SUBJECT_{i}_TEACHER", cfg["teacher"]),
        "admin": int(os.getenv(f"SUBJECT_{i}_ADMIN_ID", cfg["admin"])),
    }

ADMIN_SUBJECTS: dict[int, list[str]] = {}
for key, s in SUBJECTS.items():
    ADMIN_SUBJECTS.setdefault(s["admin"], []).append(key)
ADMIN_IDS = set(ADMIN_SUBJECTS)

REQ_LABELS = {
    "pending": "⏳ بانتظار موافقة الآنسة",
    "rejected": "❌ مرفوض",
    "approved": "✅ مقبول",
}
# حالات الاختبار عند الطالب: بدون محاولة ▫️ | exam_sent 📝 | waiting ⏳ (خلص وبينطر الحلول) | done ✅
ATT_ICONS = {None: "▫️", "exam_sent": "📝", "waiting": "⏳", "done": "✅"}
PAGE_STUDENT = 20   # عدد الاختبارات بكل صفحة عند الطالب
PAGE_ADMIN = 25     # عدد الأرقام بكل صفحة عند الآنسة

# حالات محادثة التسجيل
INFO, SUBJECT, CONFIRM = range(3)


def subject_name(key: str) -> str:
    return SUBJECTS[key]["name"]


def teacher_title(key: str) -> str:
    t = SUBJECTS[key]["teacher"]
    return f"الآنسة {t}" if t else "الآنسة"


# ---------------------------------------------------------------- قاعدة البيانات
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with closing(db()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL,
                card_number TEXT NOT NULL,
                status TEXT NOT NULL,            -- pending | rejected | approved
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_req_user ON requests(user_id);

            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES requests(id),
                exam_num INTEGER NOT NULL,
                status TEXT NOT NULL,            -- exam_sent | waiting | done
                answer_file_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (request_id, exam_num)
            );

            CREATE TABLE IF NOT EXISTS files (
                subject TEXT NOT NULL,
                exam_num INTEGER NOT NULL,
                kind TEXT NOT NULL,              -- exam | solution
                file_id TEXT NOT NULL,
                file_type TEXT NOT NULL,         -- document | photo | video
                PRIMARY KEY (subject, exam_num, kind)
            );
            """
        )
        # حالات من النسخة القديمة (تسليم بالصورة) بترجع "الاختبار عندك"
        conn.execute("UPDATE attempts SET status = 'exam_sent' WHERE status IN ('submitted', 'answer_rejected')")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def q_one(sql: str, args=()):
    with closing(db()) as conn:
        return conn.execute(sql, args).fetchone()


def q_all(sql: str, args=()):
    with closing(db()) as conn:
        return conn.execute(sql, args).fetchall()


def execute(sql: str, args=()) -> int:
    with closing(db()) as conn, conn:
        return conn.execute(sql, args).lastrowid


def get_request(req_id: int):
    return q_one("SELECT * FROM requests WHERE id = ?", (req_id,))


def user_requests(user_id: int):
    return q_all("SELECT * FROM requests WHERE user_id = ? ORDER BY id DESC", (user_id,))


def get_attempt(att_id: int):
    """المحاولة مع معلومات الطالب والمادة."""
    return q_one(
        "SELECT a.*, r.user_id, r.subject, r.status AS req_status"
        " FROM attempts a JOIN requests r ON r.id = a.request_id WHERE a.id = ?",
        (att_id,),
    )


def find_attempt(req_id: int, exam_num: int):
    return q_one("SELECT * FROM attempts WHERE request_id = ? AND exam_num = ?", (req_id, exam_num))


def set_attempt(att_id: int, status: str) -> None:
    execute("UPDATE attempts SET status = ?, updated_at = ? WHERE id = ?", (status, now(), att_id))


def get_file(subject: str, exam_num: int, kind: str):
    return q_one(
        "SELECT * FROM files WHERE subject = ? AND exam_num = ? AND kind = ?", (subject, exam_num, kind)
    )


def uploaded_nums(subject: str, kind: str) -> list[int]:
    return [r["exam_num"] for r in q_all(
        "SELECT exam_num FROM files WHERE subject = ? AND kind = ? ORDER BY exam_num", (subject, kind))]


def next_exam_num(subject: str) -> int:
    row = q_one("SELECT MAX(exam_num) m FROM files WHERE subject = ? AND kind = 'exam'", (subject,))
    return (row["m"] or 0) + 1


# ---------------------------------------------------------------- أدوات مساعدة
def e(text) -> str:
    return html.escape(str(text or ""))


def student_card(row, title: str) -> str:
    who = f"@{e(row['username'])}" if row["username"] else f"<code>{row['user_id']}</code>"
    return (
        f"<b>{title}</b>\n"
        f"👤 الاسم: {e(row['full_name'])}\n"
        f"📚 المادة: {e(subject_name(row['subject']))}\n"
        f"🪪 بطاقة الاشتراك: <code>{e(row['card_number'])}</code>\n"
        f"🔗 الحساب: {who}\n"
        f"🕒 {e(row['updated_at'])}"
    )


def reg_buttons(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قبول", callback_data=f"reg_ok:{req_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reg_no:{req_id}"),
    ]])


def finish_button(att_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ لقد أنهيت الحل", callback_data=f"fin:{att_id}")]])


def grid(buttons: list[InlineKeyboardButton], per_row: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def page_slice(items: list, page: int, size: int) -> tuple[list, int, int]:
    pages = max(1, (len(items) + size - 1) // size)
    page = min(max(page, 0), pages - 1)
    return items[page * size:(page + 1) * size], page, pages


def nav_row(page: int, pages: int, prefix: str) -> list[InlineKeyboardButton]:
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀️ السابق", callback_data=f"{prefix}:{page - 1}"))
    row.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        row.append(InlineKeyboardButton("التالي ▶️", callback_data=f"{prefix}:{page + 1}"))
    return row


async def send_stored_file(bot, chat_id: int, f, caption: str, reply_markup=None) -> None:
    if f["file_type"] == "photo":
        await bot.send_photo(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)
    elif f["file_type"] == "video":
        await bot.send_video(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)
    else:
        await bot.send_document(chat_id, f["file_id"], caption=caption, reply_markup=reply_markup)


async def close_admin_card(query, note: str) -> None:
    """يشيل الأزرار من رسالة الآنسة ويضيف شو صار."""
    msg = query.message
    try:
        await query.edit_message_text((msg.text_html or "") + f"\n\n{note}", reply_markup=None)
    except BadRequest:
        pass


async def safe_send(bot, chat_id: int, text: str, **kw) -> bool:
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except (Forbidden, BadRequest) as err:
        log.warning("send to %s failed: %s", chat_id, err)
        return False


# ---------------------------------------------------------------- الأزرار الثابتة (تحت مكان الكتابة)
BTN_EXAMS = "📚 اختباراتي"
BTN_REGISTER = "📝 تسجيل عند آنسة"
BTN_STATUS = "📋 حالة طلباتي"
BTN_UP_EXAM = "📤 رفع اختبار"
BTN_UP_SOL = "📤 رفع حلول"
BTN_PENDING = "⏳ طلبات التسجيل"
BTN_STATS = "📊 إحصائيات"
STUDENT_BTNS = (BTN_EXAMS, BTN_REGISTER, BTN_STATUS)
ADMIN_BTNS = (BTN_UP_EXAM, BTN_UP_SOL, BTN_PENDING, BTN_STATS)
MENU_FILTER = filters.Text(list(STUDENT_BTNS + ADMIN_BTNS))


def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    if user_id in ADMIN_IDS:
        rows = [[BTN_UP_EXAM, BTN_UP_SOL], [BTN_PENDING, BTN_STATS]]
    else:
        rows = [[BTN_EXAMS, BTN_REGISTER], [BTN_STATUS]]
    return ReplyKeyboardMarkup(
        [[KeyboardButton(t) for t in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="اختار من الأزرار 👇",
    )


def status_text(user_id: int) -> str:
    reqs = user_requests(user_id)
    if not reqs:
        return f"ما عندك أي طلب لسّا.\nاضغط «{BTN_REGISTER}» تحت لتبلّش."
    return "<b>طلباتك:</b>\n" + "\n".join(
        f"#{r['id']} — {e(teacher_title(r['subject']))} ({e(subject_name(r['subject']))}): {REQ_LABELS[r['status']]}"
        for r in reqs[:5]
    )


# ---------------------------------------------------------------- /start
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


async def menu_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إذا ضغط زر من القائمة بنص التسجيل: بنلغي التسجيل وبننفّذ الزر."""
    context.user_data.pop("reg", None)
    if update.message.text == BTN_REGISTER:
        return await reg_begin(update, context)
    await menu_router(update, context)
    return ConversationHandler.END


# ---------------------------------------------------------------- محادثة التسجيل
INFO_EXAMPLE = "محمد أحمد الخطيب\n12345"
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


def teacher_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👩‍🏫 الآنسة {s['teacher']} — {s['name']}", callback_data=f"subj:{k}")]
        for k, s in SUBJECTS.items()
    ])


async def reg_begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if update.callback_query:
        await update.callback_query.answer()
    if user.id in ADMIN_IDS:
        await context.bot.send_message(user.id, "حساب الآنسة ما بيقدر يسجّل كطالب.")
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


# ---------------------------------------------------------------- قرار الآنسة على التسجيل
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


# ---------------------------------------------------------------- اختبارات الطالب
def exams_menu(req, page: int = 0) -> tuple[str, InlineKeyboardMarkup | None]:
    available = uploaded_nums(req["subject"], "exam")
    if not available:
        return "لسّا ما في اختبارات مرفوعة لهي المادة. جرّب بعد شوي.", None
    status = {r["exam_num"]: r["status"] for r in q_all(
        "SELECT exam_num, status FROM attempts WHERE request_id = ?", (req["id"],))}
    items, page, pages = page_slice(available, page, PAGE_STUDENT)
    buttons = [
        InlineKeyboardButton(f"{ATT_ICONS.get(status.get(n), '📝')} اختبار {n}", callback_data=f"ex:{req['id']}:{n}")
        for n in items
    ]
    rows = grid(buttons, 2)
    nav = nav_row(page, pages, f"menu:{req['id']}")
    if nav:
        rows.append(nav)
    text = (
        f"<b>📚 اختبارات {e(subject_name(req['subject']))}</b> ({len(available)})\n"
        "اختار الاختبار:\n\n"
        "▫️ جديد   📝 عم تحلّه   ⏳ بانتظار الحلول   ✅ منتهي"
    )
    return text, InlineKeyboardMarkup(rows)


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


# ---------------------------------------------------------------- رفع الملفات (للآنسة)
KIND_LABEL = {"exam": "الاختبار", "solution": "الحلول"}


def upload_menu(subject: str, kind: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    exams = uploaded_nums(subject, "exam")
    rows: list[list[InlineKeyboardButton]] = []
    if kind == "exam":
        nxt = next_exam_num(subject)
        rows.append([InlineKeyboardButton(f"➕ إضافة اختبار جديد (رقم {nxt})", callback_data=f"upn:exam:{subject}:{nxt}")])
        nums, mark = exams, set(exams)
        text = (
            f"<b>اختبارات {e(subject_name(subject))}</b> — مرفوع: {len(exams)}\n"
            "اضغطي ➕ لتضيفي اختبار جديد، أو اختاري رقم موجود لتبدّلي ملفه."
        )
    else:
        nums, mark = exams, set(uploaded_nums(subject, "solution"))
        if not exams:
            return "لازم ترفعي اختبار أولاً، وبعدين حلوله.", InlineKeyboardMarkup([])
        missing = len([n for n in exams if n not in mark])
        text = (
            f"<b>حلول {e(subject_name(subject))}</b>\n"
            "اختاري رقم الاختبار يلي بدك ترفعي حلوله (✅ = مرفوع، ورفعه من جديد بيبدّله)."
            + (f"\n⚠️ في {missing} اختبار بلا حلول." if missing else "")
        )
    items, page, pages = page_slice(nums, page, PAGE_ADMIN)
    buttons = [
        InlineKeyboardButton(f"{'✅' if n in mark else '▫️'} {n}", callback_data=f"upn:{kind}:{subject}:{n}")
        for n in items
    ]
    rows += grid(buttons, 5)
    nav = nav_row(page, pages, f"upg:{kind}:{subject}")
    if nav:
        rows.append(nav)
    return text, InlineKeyboardMarkup(rows)


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
    await send_upload_menu(msg, subject, kind)  # لترفعي اللي بعده بسرعة


# ---------------------------------------------------------------- أوامر الآنسة الأخرى
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


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("استخدم الأزرار تحت 👇", reply_markup=main_keyboard(update.effective_user.id))


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


# ---------------------------------------------------------------- التشغيل
async def set_commands(app: Application) -> None:
    # زر "Menu" جنب مكان الكتابة
    await app.bot.set_my_commands([BotCommand("start", "القائمة الرئيسية")])


def main() -> None:
    if ":" not in BOT_TOKEN:
        sys.exit("⚠️ حط توكن البوت بـ BOT_TOKEN بأول الملف.")
    init_db()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        # الحماية: كل رسالة/ملف من البوت ممنوع تحويله أو حفظه أو نسخه
        .defaults(Defaults(parse_mode=ParseMode.HTML, protect_content=True))
        .post_init(set_commands)
        .build()
    )
    private = filters.ChatType.PRIVATE
    admins = filters.User(user_id=list(ADMIN_IDS))

    registration = ConversationHandler(
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

    app.add_handler(registration)
    app.add_handler(CommandHandler("start", start, filters=private))
    app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^(reg_ok|reg_no):\d+$"))
    app.add_handler(CallbackQueryHandler(show_menu, pattern=r"^menu:\d+(:\d+)?$"))
    app.add_handler(CallbackQueryHandler(open_exam, pattern=r"^ex:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(finish_pressed, pattern=r"^fin:\d+$"))
    app.add_handler(CallbackQueryHandler(finish_confirmed, pattern=r"^fin_yes:\d+$"))
    app.add_handler(CallbackQueryHandler(finish_back, pattern=r"^fin_no:\d+$"))
    app.add_handler(CallbackQueryHandler(upload_subject, pattern=r"^up:(exam|solution):s\d$"))
    app.add_handler(CallbackQueryHandler(upload_page, pattern=r"^upg:(exam|solution):s\d:\d+$"))
    app.add_handler(CallbackQueryHandler(upload_number, pattern=r"^upn:(exam|solution):s\d:\d+$"))
    app.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))

    app.add_handler(CommandHandler(["setexam", "setsolution"], set_file_cmd, filters=private & admins))
    app.add_handler(CommandHandler("pending", pending_cmd, filters=private & admins))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=private & admins))
    app.add_handler(MessageHandler(private & admins & (filters.Document.ALL | filters.PHOTO | filters.VIDEO), admin_file))

    app.add_handler(MessageHandler(private & ~admins & (filters.PHOTO | filters.Document.ALL | filters.VIDEO), student_media))
    app.add_handler(MessageHandler(private & MENU_FILTER, menu_router))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, fallback_text))

    log.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
