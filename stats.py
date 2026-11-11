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
import time
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import ADMIN_SUBJECTS, OWNER_ID, SUBJECTS, TZ, can_manage, subject_name, teacher_title
from database import (ACTIVE_SQL, exam_label, exam_titles, get_request, now, q_all, q_one, set_request_status,
                      uploaded_nums)
from keyboards import grid, nav_row, page_slice
from subscription import expiry_text
from utils import e, safe_send

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
    week_ago = (datetime.now(TZ) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    new_week = q_one(
        "SELECT COUNT(*) c FROM requests WHERE subject = ? AND status = 'approved' AND created_at >= ?",
        (subject, week_ago),
    )["c"]
    att = q_one(
        f"SELECT SUM(a.status IN {FINISHED}) fin, SUM(a.status = 'exam_sent') open_,"
        f" COUNT(DISTINCT CASE WHEN a.status IN {FINISHED} THEN a.request_id END) active"
        " FROM attempts a JOIN requests r ON r.id = a.request_id"
        " WHERE r.subject = ? AND r.status IN ('approved', 'expired')",
        (subject,),
    )
    exams = uploaded_nums(subject, "exam")
    sols = set(uploaded_nums(subject, "solution"))
    approved = q_one(f"SELECT COUNT(*) c FROM requests r WHERE r.subject = ? AND {ACTIVE_SQL}", (subject, now()))["c"]
    expired = reqs.get("expired", 0) + reqs.get("approved", 0) - approved  # يلي خلص وقتهم وما مرّت المهمة لسّا
    avg_score = q_one(
        "SELECT AVG(a.score) s, COUNT(a.score) n FROM attempts a JOIN requests r ON r.id = a.request_id"
        " WHERE r.subject = ? AND a.score IS NOT NULL", (subject,))
    finished = att["fin"] or 0
    return {
        "approved": approved,
        "pending": reqs.get("pending", 0),
        "rejected": reqs.get("rejected", 0),
        "revoked": reqs.get("revoked", 0),
        "expired": expired,
        "avg_score": avg_score["s"],
        "n_scores": avg_score["n"],
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
        "SELECT r.id, r.user_id, r.username, r.full_name, r.card_number, r.phone, r.created_at, r.status, r.expires_at,"
        " ROUND(AVG(a.score)) avg_score,"
        f" COALESCE(SUM(a.status IN {FINISHED}), 0) fin,"
        " COALESCE(SUM(a.status = 'exam_sent'), 0) open_,"
        " MAX(a.updated_at) last"
        " FROM requests r LEFT JOIN attempts a ON a.request_id = r.id"
        " WHERE r.subject = ? AND r.status IN ('approved', 'revoked', 'expired')"
        " GROUP BY r.id ORDER BY r.status != 'approved', fin DESC, r.full_name",
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
        f"• المشتركين حالياً: <b>{s['approved']}</b>\n"
        f"• جداد آخر 7 أيام: {s['new_week']}\n"
        f"• بانتظار الموافقة: {s['pending']} | مرفوضين: {s['rejected']}\n"
        f"• منتهي اشتراكهم: {s['expired']} | موقوفين: {s['revoked']}\n"
        f"• خلّصوا اختبار واحد عالأقل: {s['active']}\n\n"
        f"<b>📝 الاختبارات</b>\n"
        f"• مجموع الاختبارات المنتهية: <b>{s['finished']}</b>\n"
        f"• معدّل لكل طالب: {s['avg']:.1f}\n"
        f"• مفتوحة وما خلصت: {s['open']}\n"
        + (f"• معدّل الدرجات: <b>{s['avg_score']:.0f}</b>/100 (من {s['n_scores']} درجة سجّلها الطلاب)\n\n"
           if s["n_scores"] else "• معدّل الدرجات: لسّا ما حدا سجّل درجته\n\n") +
        "<b>📂 الملفات</b>\n"
        f"• اختبارات مرفوعة: {s['exams']} | حلول مرفوعة: {s['solutions']}"
    )
    if s["missing"]:
        text += f"\n⚠️ بلا حلول: {nums_text(s['missing'], 30)}"
    rows = [
        [InlineKeyboardButton("👥 قائمة الطلاب", callback_data=f"stl:{subject}:0"),
         InlineKeyboardButton("📝 تفصيل الاختبارات", callback_data=f"ste:{subject}:0")],
        [InlineKeyboardButton("🔎 بحث عن طالب", callback_data=f"sfs:{subject}"),
         InlineKeyboardButton("📥 تنزيل ملف Excel", callback_data=f"stc:{subject}")],
    ]
    if len(allowed_subjects(user_id)) > 1:
        rows.append([InlineKeyboardButton("↩️ كل المواد", callback_data="sto")])
    return text, InlineKeyboardMarkup(rows)


def overview_screen(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """لصاحب البوت (أو آنسة عندها أكتر من مادة)."""
    subjects = allowed_subjects(user_id)
    lines = ["<b>📊 الإحصائيات</b>\n"]
    if OWNER_ID and user_id == OWNER_ID:
        total = q_one(f"SELECT COUNT(DISTINCT r.user_id) c FROM requests r WHERE {ACTIVE_SQL}", (now(),))["c"]
        everyone = q_one("SELECT COUNT(DISTINCT user_id) c FROM requests")["c"]
        lines.append(f"👥 مجموع الطلاب المشتركين حالياً على البوت: <b>{total}</b>")
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
        expired = st["status"] == "expired" or (st["status"] == "approved" and st["expires_at"] and st["expires_at"] <= now())
        mark = "🚫 " if st["status"] == "revoked" else ("⌛ " if expired else "")
        score = f" | 🎯 {st['avg_score']:.0f}" if st["avg_score"] is not None else ""
        lines.append(f"{i}. {mark}{e(st['full_name'])} — 🪪 {e(st['card_number'])} — ✅ {st['fin']}{open_part}{score}")
    lines.append("\n✅ منتهية   📝 مفتوحة   🎯 معدّل الدرجات   ⌛ خلص اشتراكه   🚫 موقوف")
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


def student_detail_screen(req, page: int, viewer: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    subject = req["subject"]
    finished, opened = student_exams(req["id"])
    total = len(uploaded_nums(subject, "exam"))
    not_started = max(total - len(finished) - len(opened), 0)
    last = q_one("SELECT MAX(updated_at) m FROM attempts WHERE request_id = ?", (req["id"],))["m"]
    who = f"@{e(req['username'])}" if req["username"] else f"<code>{req['user_id']}</code>"
    text = (
        f"<b>👤 {e(req['full_name'])}</b>\n"
        f"🪪 بطاقة الاشتراك: <code>{e(req['card_number'])}</code>\n"
        + (f"📱 الهاتف: <code>{e(req['phone'])}</code>\n" if req["phone"] else "") +
        f"🆔 ID تلغرام: <code>{req['user_id']}</code>\n"
        f"🔗 الحساب: {who}\n"
        f"📅 تاريخ التسجيل: {e(req['created_at'])}\n"
        f"🕒 آخر نشاط: {e(last or 'ما فتح أي اختبار')}\n"
        f"📅 الاشتراك: {expiry_text(req)}\n\n"
        f"✅ <b>خلّص {len(finished)} من {total}</b> اختبار\n"
        f"   الأرقام: {nums_text(finished)}\n"
        f"📝 مفتوحة وما خلصت: {len(opened)}"
        + (f" ({nums_text(opened)})" if opened else "")
        + f"\n▫️ ما بلّش فيها: {not_started}"
    )
    scores = q_all("SELECT exam_num, score FROM attempts WHERE request_id = ? AND score IS NOT NULL ORDER BY exam_num",
                   (req["id"],))
    if scores:
        avg = sum(r["score"] for r in scores) / len(scores)
        text += f"\n\n🎯 <b>الدرجات</b> (المعدّل {avg:.0f}/100):\n" + "، ".join(
            f"{r['exam_num']}: {r['score']}" for r in scores[:40])
    if req["status"] == "revoked":
        text = "🚫 <b>هاد الطالب موقوف</b> — ما بيقدر يفتح اختبارات ولا ياخد حلول.\n\n" + text
    rows = []
    if viewer and can_manage(viewer, subject):
        if req["status"] in ("approved", "expired"):
            rows.append([InlineKeyboardButton("⏳ تمديد / تجديد الاشتراك", callback_data=f"extm:{req['id']}:{page}")])
            rows.append([InlineKeyboardButton("🚫 إيقاف الطالب", callback_data=f"srv:{req['id']}:{page}")])
        elif req["status"] == "revoked":
            rows.append([InlineKeyboardButton("♻️ إعادة تفعيل الطالب", callback_data=f"srs:{req['id']}:{page}")])
    rows.append([InlineKeyboardButton("↩️ قائمة الطلاب", callback_data=f"stl:{subject}:{page}")])
    return text, InlineKeyboardMarkup(rows)


def exams_screen(subject: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    exams = uploaded_nums(subject, "exam")
    back = [InlineKeyboardButton("↩️ الملخص", callback_data=f"sts:{subject}")]
    if not exams:
        return "لسّا ما في اختبارات مرفوعة.", InlineKeyboardMarkup([back])
    sols = set(uploaded_nums(subject, "solution"))
    counts = {r["exam_num"]: r for r in q_all(
        f"SELECT a.exam_num, COUNT(*) opened, SUM(a.status IN {FINISHED}) fin, AVG(a.score) avg_score"
        " FROM attempts a JOIN requests r ON r.id = a.request_id"
        " WHERE r.subject = ? GROUP BY a.exam_num",
        (subject,),
    )}
    titles = exam_titles(subject)
    items, page, pages = page_slice(exams, page, PAGE_EXAMS)
    lines = [f"<b>📝 تفصيل اختبارات {e(subject_name(subject))}</b>\n"]
    for n in items:
        c = counts.get(n)
        fin, opened = (c["fin"] or 0, c["opened"]) if c else (0, 0)
        sol = "" if n in sols else "  ⚠️ بلا حلول"
        avg = f" | 🎯 {c['avg_score']:.0f}" if c and c["avg_score"] is not None else ""
        lines.append(f"<b>{e(exam_label(subject, n, titles))}</b>: ✅ {fin} خلّصوه | 👀 {opened} فتحوه{avg}{sol}")
    rows = []
    nav = nav_row(page, pages, f"ste:{subject}")
    if nav:
        rows.append(nav)
    rows.append(back)
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def students_csv(subject: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#", "الاسم", "رقم بطاقة الاشتراك", "الهاتف", "حساب تلغرام", "ID تلغرام", "تاريخ التسجيل",
                "الحالة", "ينتهي الاشتراك", "عدد الاختبارات المنتهية", "أرقام الاختبارات المنتهية",
                "مفتوحة وما خلصت", "معدّل الدرجات", "آخر نشاط"])
    for i, st in enumerate(students_of(subject), start=1):
        finished, opened = student_exams(st["id"])
        w.writerow([
            i, st["full_name"], st["card_number"], st["phone"] or "",
            f"@{st['username']}" if st["username"] else "", st["user_id"],
            st["created_at"], st["status"], st["expires_at"] or "بدون انتهاء", len(finished),
            " ".join(map(str, finished)), " ".join(map(str, opened)),
            "" if st["avg_score"] is None else int(st["avg_score"]), st["last"] or "",
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
        await _show(query, *student_detail_screen(req, int(parts[2]), uid))
        return

    if kind in ("srv", "srvy", "srs"):
        req = get_request(int(parts[1]))
        page = int(parts[2])
        if not req or not can_manage(uid, req["subject"]):
            await query.answer("مو مسموح.", show_alert=True)
            return
        if kind == "srv":  # تأكيد قبل الإيقاف
            await query.answer()
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🚫 نعم، أوقفه", callback_data=f"srvy:{req['id']}:{page}"),
                InlineKeyboardButton("↩️ لا", callback_data=f"std:{req['id']}:{page}"),
            ]])
            await _show(query, f"متأكدة بدك توقفي <b>{e(req['full_name'])}</b>؟\n"
                               "ما رح يقدر يفتح اختبارات ولا ياخد حلول، لحد ما تعيدي تفعيله.", kb)
            return
        if kind == "srvy":
            changed = set_request_status(req["id"], "revoked", only_from=("approved", "expired"))
            note = "🚫 تم إيقاف الطالب." if changed else "الحالة تغيّرت من قبل."
            if changed:
                await safe_send(context.bot, req["user_id"],
                                f"🚫 تم إيقاف اشتراكك في مادة {e(subject_name(req['subject']))}.\n"
                                f"للاستفسار تواصل مع {e(teacher_title(req['subject']))}.")
        else:
            changed = set_request_status(req["id"], "approved", only_from=("revoked",))
            note = "♻️ تم إعادة تفعيل الطالب." if changed else "الحالة تغيّرت من قبل."
            if changed:
                await safe_send(context.bot, req["user_id"],
                                f"✅ أُعيد تفعيل اشتراكك في مادة {e(subject_name(req['subject']))}.")
        await query.answer(note, show_alert=True)
        await _show(query, *student_detail_screen(get_request(req["id"]), page, uid))
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
    elif kind == "sfs":
        await query.answer()
        await ask_search(context, uid, subject)
    elif kind == "stc":
        await query.answer("عم جهّز الملف...")
        data = students_csv(subject)
        name = f"students_{subject}_{datetime.now(TZ):%Y-%m-%d}.csv"
        await context.bot.send_document(
            uid, io.BytesIO(data), filename=name,
            caption=f"📥 طلاب {e(subject_name(subject))} — بينفتح بـ Excel",
            protect_content=False,  # هاد الملف للآنسة، فمسموح تحفظه
        )


# ---------------------------------------------------------------- البحث عن طالب
async def ask_search(context: ContextTypes.DEFAULT_TYPE, user_id: int, subject: str | None = None) -> None:
    context.user_data.pop("upload", None)
    context.user_data["await"] = {"kind": "search", "subject": subject, "t": time.time()}
    await context.bot.send_message(
        user_id, "🔎 اكتبي اسم الطالب (أو جزء منه)، أو رقم بطاقته، أو رقم هاتفه، أو الـ ID تبعه:\n\n"
                 "للإلغاء اضغطي أي زر من تحت."
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed_subjects(update.effective_user.id):
        await ask_search(context, update.effective_user.id)


async def search_received(update: Update, context: ContextTypes.DEFAULT_TYPE, wait: dict) -> None:
    uid = update.effective_user.id
    subjects = [wait["subject"]] if wait.get("subject") else allowed_subjects(uid)
    subjects = [s for s in subjects if can_view(uid, s)]
    term = " ".join((update.message.text or "").split())[:40]
    if len(term) < 2 or not subjects:
        context.user_data["await"] = wait
        await update.message.reply_text("⚠️ اكتبي حرفين عالأقل.")
        return
    digits = "".join(ch for ch in term if ch.isdigit())
    digits = digits[-9:] if len(digits) >= 7 else ""  # آخر 9 أرقام: بيلاقي 0933... و+963933... متل بعض
    like = "%" + term.replace("\\", "").replace("%", "").replace("_", "") + "%"
    marks = ",".join("?" * len(subjects))
    rows = q_all(
        f"SELECT * FROM requests WHERE subject IN ({marks}) AND status != 'rejected'"
        " AND (full_name LIKE ? OR card_number = ? OR username = ? OR CAST(user_id AS TEXT) = ?"
        " OR (? != '' AND phone LIKE ?)) ORDER BY full_name LIMIT 15",
        (*subjects, like, term, term.lstrip("@"), term, digits, f"%{digits}"),
    )
    if not rows:
        context.user_data["await"] = wait
        await update.message.reply_text("ما لقيت حدا 🤷‍♀️ جرّبي كلمة تانية، أو اضغطي أي زر من تحت للإلغاء.")
        return
    from config import REQ_LABELS
    lines = [f"🔎 النتائج ({len(rows)}):"]
    buttons = []
    for i, r in enumerate(rows, start=1):
        lines.append(f"{i}. {e(r['full_name'])} — 🪪 {e(r['card_number'])} — {e(subject_name(r['subject']))}"
                     f" — {REQ_LABELS.get(r['status'], r['status'])}")
        buttons.append(InlineKeyboardButton(str(i), callback_data=f"std:{r['id']}:0"))
    await update.message.reply_text("\n".join(lines) + "\n\nاضغطي الرقم لتشوفي التفاصيل.",
                                    reply_markup=InlineKeyboardMarkup(grid(buttons, 5)))
