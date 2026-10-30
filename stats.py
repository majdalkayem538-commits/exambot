"""الإحصائيات.

كل آنسة بتشوف مقررها بس:
  ملخص المادة ← قائمة الطلاب (مع كم اختبار خلّص كل واحد) ← تفاصيل أي طالب
             ← تفصيل الاختبارات (كم طالب فتح/خلّص كل اختبار)
             ← تنزيل ملف Excel (CSV) بكل الطلاب
صاحب البوت (OWNER_ID، اختياري) بيشوف كل المواد ومجموع الطلاب على البوت.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import ADMIN_SUBJECTS, OWNER_ID, SUBJECTS, subject_name, teacher_title
from database import get_request, q_all, q_one, uploaded_nums
from keyboards import grid, nav_row, page_slice
from utils import e

PAGE_STUDENTS = 15   # عدد الطلاب بكل صفحة
PAGE_EXAMS = 30      # عدد الاختبارات بكل صفحة بتفصيل الاختبارات
FINISHED = "('waiting', 'done')"  # الطالب ضغط «لقد أنهيت الحل»


# ---------------------------------------------------------------- الصلاحيات
def allowed_subjects(user_id: int) -> list[str]:
    """الآنسة بتشوف موادها بس، وصاحب البوت بيشوف الكل."""
    if OWNER_ID and user_id == OWNER_ID:
        return list(SUBJECTS)
    return ADMIN_SUBJECTS.get(user_id, [])


def can_view(user_id: int, subject: str) -> bool:
    return subject in allowed_subjects(user_id)


# ---------------------------------------------------------------- الاستعلامات
def subject_summary(subject: str) -> dict:
    reqs = {r["status"]: r["c"] for r in q_all(
        "SELECT status, COUNT(*) c FROM requests WHERE subject = ? GROUP BY status", (subject,))}
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    new_week = q_one(
        "SELECT COUNT(*) c FROM requests WHERE subject = ? AND status = 'approved' AND created_at >= ?",
        (subject, week_ago),
    )["c"]
    att = q_one(
        f"SELECT SUM(a.status IN {FINISHED}) fin, SUM(a.status = 'exam_sent') open_,"
        f" COUNT(DISTINCT CASE WHEN a.status IN {FINISHED} THEN a.request_id END) active"
        " FROM attempts a JOIN requests r ON r.id = a.request_id"
        " WHERE r.subject = ? AND r.status = 'approved'",
        (subject,),
    )
    exams = uploaded_nums(subject, "exam")
    sols = set(uploaded_nums(subject, "solution"))
    approved = reqs.get("approved", 0)
    finished = att["fin"] or 0
    return {
        "approved": approved,
        "pending": reqs.get("pending", 0),
        "rejected": reqs.get("rejected", 0),
        "new_week": new_week,
        "finished": finished,
        "open": att["open_"] or 0,
        "active": att["active"] or 0,
        "avg": (finished / approved) if approved else 0,
        "exams": len(exams),
        "solutions": len(sols),
        "missing": [n for n in exams if n not in sols],
    }


def students_of(subject: str):
    """الطلاب المقبولين مع عدد الاختبارات يلي خلّصوها، الأكتر أولاً."""
    return q_all(
        "SELECT r.id, r.user_id, r.username, r.full_name, r.card_number, r.created_at,"
        f" COALESCE(SUM(a.status IN {FINISHED}), 0) fin,"
        " COALESCE(SUM(a.status = 'exam_sent'), 0) open_,"
        " MAX(a.updated_at) last"
        " FROM requests r LEFT JOIN attempts a ON a.request_id = r.id"
        " WHERE r.subject = ? AND r.status = 'approved'"
        " GROUP BY r.id ORDER BY fin DESC, r.full_name",
        (subject,),
    )


def student_exams(req_id: int) -> tuple[list[int], list[int]]:
    rows = q_all("SELECT exam_num, status FROM attempts WHERE request_id = ? ORDER BY exam_num", (req_id,))
    finished = [r["exam_num"] for r in rows if r["status"] in ("waiting", "done")]
    opened = [r["exam_num"] for r in rows if r["status"] == "exam_sent"]
    return finished, opened


def nums_text(nums: list[int], limit: int = 60) -> str:
    if not nums:
        return "—"
    text = "، ".join(map(str, nums[:limit]))
    return text + (" ..." if len(nums) > limit else "")


# ---------------------------------------------------------------- الشاشات
def summary_screen(subject: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    s = subject_summary(subject)
    text = (
        f"<b>📊 {e(subject_name(subject))} — {e(teacher_title(subject))}</b>\n\n"
        f"<b>👥 الطلاب</b>\n"
        f"• المسجّلين (مقبولين): <b>{s['approved']}</b>\n"
        f"• جداد آخر 7 أيام: {s['new_week']}\n"
        f"• بانتظار الموافقة: {s['pending']} | مرفوضين: {s['rejected']}\n"
        f"• خلّصوا اختبار واحد عالأقل: {s['active']}\n\n"
        f"<b>📝 الاختبارات</b>\n"
        f"• مجموع الاختبارات المنتهية: <b>{s['finished']}</b>\n"
        f"• معدّل لكل طالب: {s['avg']:.1f}\n"
        f"• مفتوحة وما خلصت: {s['open']}\n\n"
        f"<b>📂 الملفات</b>\n"
        f"• اختبارات مرفوعة: {s['exams']} | حلول مرفوعة: {s['solutions']}"
    )
    if s["missing"]:
        text += f"\n⚠️ بلا حلول: {nums_text(s['missing'], 30)}"
    rows = [
        [InlineKeyboardButton("👥 قائمة الطلاب", callback_data=f"stl:{subject}:0"),
         InlineKeyboardButton("📝 تفصيل الاختبارات", callback_data=f"ste:{subject}:0")],
        [InlineKeyboardButton("📥 تنزيل ملف Excel", callback_data=f"stc:{subject}")],
    ]
    if len(allowed_subjects(user_id)) > 1:
        rows.append([InlineKeyboardButton("↩️ كل المواد", callback_data="sto")])
    return text, InlineKeyboardMarkup(rows)


def overview_screen(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """لصاحب البوت (أو آنسة عندها أكتر من مادة)."""
    subjects = allowed_subjects(user_id)
    lines = ["<b>📊 الإحصائيات</b>\n"]
    if OWNER_ID and user_id == OWNER_ID:
        total = q_one("SELECT COUNT(DISTINCT user_id) c FROM requests WHERE status = 'approved'")["c"]
        everyone = q_one("SELECT COUNT(DISTINCT user_id) c FROM requests")["c"]
        lines.append(f"👥 مجموع الطلاب المقبولين على البوت: <b>{total}</b>")
        lines.append(f"🔢 كل يلي قدّموا طلب (مع المعلّقين والمرفوضين): {everyone}\n")
    for subj in subjects:
        s = subject_summary(subj)
        lines.append(
            f"<b>{e(subject_name(subj))}</b> — {e(teacher_title(subj))}\n"
            f"   👥 {s['approved']} طالب | ✅ {s['finished']} اختبار منتهي | 📂 {s['exams']} اختبار"
        )
    lines.append("\nاختار مادة للتفاصيل 👇")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📚 {subject_name(s)}", callback_data=f"sts:{s}")] for s in subjects
    ])
    return "\n".join(lines), kb


def students_screen(subject: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    students = list(students_of(subject))
    back = [InlineKeyboardButton("↩️ الملخص", callback_data=f"sts:{subject}")]
    if not students:
        return f"<b>👥 طلاب {e(subject_name(subject))}</b>\n\nلسّا ما في طلاب مقبولين.", InlineKeyboardMarkup([back])
    items, page, pages = page_slice(students, page, PAGE_STUDENTS)
    start_i = page * PAGE_STUDENTS
    lines = [
        f"<b>👥 طلاب {e(subject_name(subject))}</b> ({len(students)})",
        "مرتّبين حسب عدد الاختبارات المنتهية. اضغط الرقم لتشوف التفاصيل.\n",
    ]
    for i, st in enumerate(items, start=start_i + 1):
        open_part = f" | 📝 {st['open_']}" if st["open_"] else ""
        lines.append(f"{i}. {e(st['full_name'])} — 🪪 {e(st['card_number'])} — ✅ {st['fin']}{open_part}")
    lines.append("\n✅ اختبارات منتهية   📝 مفتوحة وما خلصت")
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"std:{st['id']}:{page}")
        for i, st in enumerate(items, start=start_i + 1)
    ]
    rows = grid(buttons, 5)
    nav = nav_row(page, pages, f"stl:{subject}")
    if nav:
        rows.append(nav)
    rows.append(back)
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def student_detail_screen(req, page: int) -> tuple[str, InlineKeyboardMarkup]:
    subject = req["subject"]
    finished, opened = student_exams(req["id"])
    total = len(uploaded_nums(subject, "exam"))
    not_started = max(total - len(finished) - len(opened), 0)
    last = q_one("SELECT MAX(updated_at) m FROM attempts WHERE request_id = ?", (req["id"],))["m"]
    who = f"@{e(req['username'])}" if req["username"] else f"<code>{req['user_id']}</code>"
    text = (
        f"<b>👤 {e(req['full_name'])}</b>\n"
        f"🪪 بطاقة الاشتراك: <code>{e(req['card_number'])}</code>\n"
        f"🔗 الحساب: {who}\n"
        f"📅 تاريخ التسجيل: {e(req['created_at'])}\n"
        f"🕒 آخر نشاط: {e(last or 'ما فتح أي اختبار')}\n\n"
        f"✅ <b>خلّص {len(finished)} من {total}</b> اختبار\n"
        f"   الأرقام: {nums_text(finished)}\n"
        f"📝 مفتوحة وما خلصت: {len(opened)}"
        + (f" ({nums_text(opened)})" if opened else "")
        + f"\n▫️ ما بلّش فيها: {not_started}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ قائمة الطلاب", callback_data=f"stl:{subject}:{page}")]])
    return text, kb


def exams_screen(subject: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    exams = uploaded_nums(subject, "exam")
    back = [InlineKeyboardButton("↩️ الملخص", callback_data=f"sts:{subject}")]
    if not exams:
        return "لسّا ما في اختبارات مرفوعة.", InlineKeyboardMarkup([back])
    sols = set(uploaded_nums(subject, "solution"))
    counts = {r["exam_num"]: r for r in q_all(
        f"SELECT a.exam_num, COUNT(*) opened, SUM(a.status IN {FINISHED}) fin"
        " FROM attempts a JOIN requests r ON r.id = a.request_id"
        " WHERE r.subject = ? AND r.status = 'approved' GROUP BY a.exam_num",
        (subject,),
    )}
    items, page, pages = page_slice(exams, page, PAGE_EXAMS)
    lines = [f"<b>📝 تفصيل اختبارات {e(subject_name(subject))}</b>\n"]
    for n in items:
        c = counts.get(n)
        fin, opened = (c["fin"] or 0, c["opened"]) if c else (0, 0)
        sol = "" if n in sols else "  ⚠️ بلا حلول"
        lines.append(f"اختبار {n}: ✅ {fin} خلّصوه | 👀 {opened} فتحوه{sol}")
    rows = []
    nav = nav_row(page, pages, f"ste:{subject}")
    if nav:
        rows.append(nav)
    rows.append(back)
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def students_csv(subject: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#", "الاسم", "رقم بطاقة الاشتراك", "حساب تلغرام", "تاريخ التسجيل",
                "عدد الاختبارات المنتهية", "أرقام الاختبارات المنتهية", "مفتوحة وما خلصت", "آخر نشاط"])
    for i, st in enumerate(students_of(subject), start=1):
        finished, opened = student_exams(st["id"])
        w.writerow([
            i, st["full_name"], st["card_number"], f"@{st['username']}" if st["username"] else st["user_id"],
            st["created_at"], len(finished), " ".join(map(str, finished)), " ".join(map(str, opened)),
            st["last"] or "",
        ])
    return buf.getvalue().encode("utf-8-sig")  # utf-8-sig لحتى يفتح Excel العربي صح


# ---------------------------------------------------------------- الهاندلرز
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    subjects = allowed_subjects(uid)
    if not subjects:
        return
    if len(subjects) == 1:
        text, kb = summary_screen(subjects[0], uid)
    else:
        text, kb = overview_screen(uid)
    await update.message.reply_text(text, reply_markup=kb)


async def _show(query, text: str, kb: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest:
        pass  # نفس المحتوى، ما في شي يتغيّر


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    uid = query.from_user.id
    parts = query.data.split(":")
    kind = parts[0]

    if kind == "sto":
        if not allowed_subjects(uid):
            await query.answer("مو مسموح.", show_alert=True)
            return
        await query.answer()
        await _show(query, *overview_screen(uid))
        return

    if kind == "std":
        req = get_request(int(parts[1]))
        if not req or not can_view(uid, req["subject"]):
            await query.answer("مو مسموح.", show_alert=True)
            return
        await query.answer()
        await _show(query, *student_detail_screen(req, int(parts[2])))
        return

    subject = parts[1]
    if not can_view(uid, subject):
        await query.answer("هي المادة مو إلك.", show_alert=True)
        return

    if kind == "sts":
        await query.answer()
        await _show(query, *summary_screen(subject, uid))
    elif kind == "stl":
        await query.answer()
        await _show(query, *students_screen(subject, int(parts[2])))
    elif kind == "ste":
        await query.answer()
        await _show(query, *exams_screen(subject, int(parts[2])))
    elif kind == "stc":
        await query.answer("عم جهّز الملف...")
        data = students_csv(subject)
        name = f"students_{subject}_{datetime.now():%Y-%m-%d}.csv"
        await context.bot.send_document(
            uid, io.BytesIO(data), filename=name,
            caption=f"📥 طلاب {e(subject_name(subject))} — بينفتح بـ Excel",
            protect_content=False,  # هاد الملف للآنسة، فمسموح تحفظه
        )
