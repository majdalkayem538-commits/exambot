"""كل الأزرار: الأزرار الثابتة تحت مكان الكتابة، والأزرار يلي تحت الرسائل."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import filters

from config import ADMIN_IDS, ATT_ICONS, PAGE_ADMIN, PAGE_STUDENT, SUBJECTS, subject_name
from database import next_exam_num, q_all, uploaded_nums
from utils import e

# ---------------------------------------------------------------- الأزرار الثابتة (تحت مكان الكتابة)
BTN_EXAMS = "📚 اختباراتي"
BTN_REGISTER = "📝 تسجيل على مقرر"
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


# ---------------------------------------------------------------- أزرار تحت الرسائل
def reg_buttons(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قبول", callback_data=f"reg_ok:{req_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reg_no:{req_id}"),
    ]])


def finish_button(att_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ لقد أنهيت الحل", callback_data=f"fin:{att_id}")]])


def teacher_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👩‍🏫 الآنسة {s['teacher']} — {s['name']}", callback_data=f"subj:{k}")]
        for k, s in SUBJECTS.items()
    ])


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
