"""كل الأزرار: الأزرار الثابتة تحت مكان الكتابة، والأزرار يلي تحت الرسائل."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import filters

from config import ADMIN_IDS, ATT_ICONS, DURATION_CHOICES, OWNER_ID, PAGE_ADMIN, PAGE_STUDENT, SUBJECTS, duration_label, subject_name
from database import default_days, exam_label, exam_titles, next_exam_num, q_all, uploaded_nums
from utils import e

# ---------------------------------------------------------------- الأزرار الثابتة (تحت مكان الكتابة)
# الطالب (بالفصحى)
BTN_EXAMS = "📚 اختباراتي"
BTN_REGISTER = "📝 التسجيل في مادة"
BTN_STATUS = "📋 حالة اشتراكي"
# الآنسة
BTN_UP_EXAM = "📤 رفع اختبار"
BTN_UP_SOL = "📤 رفع حلول"
BTN_PENDING = "⏳ طلبات التسجيل"
BTN_STATS = "📊 إحصائيات"
BTN_BROADCAST = "📣 رسالة للطلاب"
BTN_SEARCH = "🔎 بحث عن طالب"
BTN_SETTINGS = "⚙️ الإعدادات"
# صاحب البوت
BTN_BACKUP = "💾 نسخة احتياطية"
BTN_RESTORE = "♻️ استرجاع نسخة"

# أزرار النسخ القديمة (لسّا ممكن تكون ظاهرة عند الطلاب لحد ما يبعتوا /start)
LEGACY = {"📝 تسجيل عند آنسة": BTN_REGISTER, "📋 حالة طلباتي": BTN_STATUS}

STUDENT_BTNS = (BTN_EXAMS, BTN_REGISTER, BTN_STATUS, *LEGACY)
ADMIN_BTNS = (BTN_UP_EXAM, BTN_UP_SOL, BTN_PENDING, BTN_STATS, BTN_BROADCAST, BTN_SEARCH, BTN_SETTINGS)
OWNER_BTNS = (BTN_BACKUP, BTN_RESTORE)
MENU_FILTER = filters.Text(list(STUDENT_BTNS + ADMIN_BTNS + OWNER_BTNS))
REGISTER_FILTER = filters.Text([BTN_REGISTER, *[k for k, v in LEGACY.items() if v == BTN_REGISTER]])


def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows: list[list[str]] = []
    if user_id in ADMIN_IDS:
        rows += [[BTN_UP_EXAM, BTN_UP_SOL], [BTN_PENDING, BTN_STATS], [BTN_BROADCAST, BTN_SEARCH], [BTN_SETTINGS]]
    if OWNER_ID and user_id == OWNER_ID:
        if user_id not in ADMIN_IDS:
            rows.append([BTN_STATS, BTN_SEARCH])
            rows.append([BTN_SETTINGS])
        rows.append([BTN_BACKUP, BTN_RESTORE])
    if not rows:
        rows = [[BTN_EXAMS, BTN_REGISTER], [BTN_STATUS]]
    return ReplyKeyboardMarkup(
        [[KeyboardButton(t) for t in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="اختر من الأزرار 👇",
    )


# ---------------------------------------------------------------- أزرار تحت الرسائل
def reg_buttons(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قبول", callback_data=f"reg_ok:{req_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reg_no:{req_id}"),
    ]])


def duration_picker(prefix: str, target: str, subject: str, back: str | None = None) -> InlineKeyboardMarkup:
    """أزرار مدة الاشتراك. prefix:target:days — مثلاً ap:12:30 (قبول الطلب 12 لمدة 30 يوم)."""
    d = default_days(subject)
    rows = [[InlineKeyboardButton(f"⭐ الافتراضي: {duration_label(d)}", callback_data=f"{prefix}:{target}:{d}")]]
    buttons = [InlineKeyboardButton(label, callback_data=f"{prefix}:{target}:{days}") for days, label in DURATION_CHOICES]
    rows += grid(buttons, 2)
    rows.append([InlineKeyboardButton("✍️ عدد أيام مخصّص", callback_data=f"{prefix}c:{target}")])
    if back:
        rows.append([InlineKeyboardButton("↩️ رجوع", callback_data=back)])
    return InlineKeyboardMarkup(rows)


def finish_button(att_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ لقد أنهيت الحل", callback_data=f"fin:{att_id}")]])


def score_button(att_id: int, has_score: bool = False) -> InlineKeyboardMarkup:
    label = "✏️ تعديل درجتي" if has_score else "📝 تسجيل درجتي"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"sc:{att_id}")]])


def renew_button(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 طلب تجديد الاشتراك", callback_data=f"renew:{req_id}")]])


def teacher_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👩‍🏫 الآنسة {s['teacher']} — {s['name']}", callback_data=f"subj:{k}")]
        for k, s in SUBJECTS.items()
    ])


def subject_chooser(prefix: str, subjects: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(subject_name(s), callback_data=f"{prefix}:{s}")] for s in subjects])


# ---------------------------------------------------------------- الصفحات (للاختبارات الكتيرة)
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


# ---------------------------------------------------------------- قوائم الاختبارات
def exams_menu(req, page: int = 0) -> tuple[str, InlineKeyboardMarkup | None]:
    available = uploaded_nums(req["subject"], "exam")
    if not available:
        return "لا توجد اختبارات منشورة في هذه المادة حتى الآن.", None
    rows_db = q_all("SELECT exam_num, status, score FROM attempts WHERE request_id = ?", (req["id"],))
    status = {r["exam_num"]: r["status"] for r in rows_db}
    scores = {r["exam_num"]: r["score"] for r in rows_db if r["score"] is not None}
    titles = exam_titles(req["subject"])
    items, page, pages = page_slice(available, page, PAGE_STUDENT)
    buttons = []
    for n in items:
        label = f"{ATT_ICONS.get(status.get(n), '📝')} {exam_label(req['subject'], n, titles)}"
        if n in scores:
            label += f" ({scores[n]})"
        buttons.append(InlineKeyboardButton(label[:60], callback_data=f"ex:{req['id']}:{n}"))
    rows = grid(buttons, 1 if titles else 2)
    nav = nav_row(page, pages, f"menu:{req['id']}")
    if nav:
        rows.append(nav)
    text = (
        f"<b>📚 اختبارات مادة {e(subject_name(req['subject']))}</b> ({len(available)})\n"
        "اختر الاختبار:\n\n"
        "▫️ جديد   📝 قيد الحل   ⏳ بانتظار الحلول   ✅ منتهٍ (الرقم بين القوسين درجتك)"
    )
    return text, InlineKeyboardMarkup(rows)


KIND_LABEL = {"exam": "الاختبار", "solution": "الحلول"}


def upload_menu(subject: str, kind: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    exams = uploaded_nums(subject, "exam")
    titles = exam_titles(subject)
    rows: list[list[InlineKeyboardButton]] = []
    if kind == "exam":
        nxt = next_exam_num(subject)
        rows.append([InlineKeyboardButton(f"➕ إضافة اختبار جديد (رقم {nxt})", callback_data=f"upn:exam:{subject}:new")])
        nums, mark = exams, set(exams)
        text = (
            f"<b>اختبارات {e(subject_name(subject))}</b> — مرفوع: {len(exams)}\n"
            "اضغطي ➕ لتضيفي اختبار جديد، أو اختاري رقم موجود لتبدّلي ملفه.\n"
            "💡 فيكي تكتبي عنوان الاختبار كتعليق (caption) على الملف."
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
    if kind == "exam" and exams:
        rows.append([InlineKeyboardButton("🏷 عناوين الاختبارات", callback_data=f"ttm:{subject}:0"),
                     InlineKeyboardButton("🗑 حذف اختبار", callback_data=f"dlm:{subject}:0")])
    if titles and kind == "exam":
        text += "\n\n" + "\n".join(f"• {e(exam_label(subject, n, titles))}" for n in items if n in titles)
    return text, InlineKeyboardMarkup(rows)


def exam_picker(subject: str, prefix: str, page: int, back: str) -> tuple[list[list[InlineKeyboardButton]], int]:
    """أزرار أرقام الاختبارات الموجودة (للحذف أو التسمية)."""
    exams = uploaded_nums(subject, "exam")
    items, page, pages = page_slice(exams, page, PAGE_ADMIN)
    rows = grid([InlineKeyboardButton(str(n), callback_data=f"{prefix}:{subject}:{n}") for n in items], 5)
    nav = nav_row(page, pages, f"{prefix[:2]}m:{subject}")
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data=back)])
    return rows, len(exams)
