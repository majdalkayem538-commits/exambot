"""قاعدة البيانات (SQLite): الطلبات والاشتراكات، الاختبارات وعناوينها، المحاولات والدرجات، الإعدادات."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta

from config import DB_PATH, TZ

TIME_FMT = "%Y-%m-%d %H:%M"
ACTIVE_SQL = "(r.status = 'approved' AND (r.expires_at IS NULL OR r.expires_at > ?))"  # مع now()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column(conn, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    try:
        os.makedirs(folder, exist_ok=True)
        if not os.access(folder, os.W_OK):
            raise PermissionError(folder)
    except OSError:
        sys.exit(
            f"⚠️ ما في صلاحية كتابة على المجلد {folder}.\n"
            "على Render: ضيف Disk من تبويب Disks وخلّي الـ Mount Path نفس مجلد DB_PATH (مثلاً /var/data)."
        )
    with closing(db()) as conn, conn:
        conn.executescript(
            """
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL,
                card_number TEXT NOT NULL,
                status TEXT NOT NULL,            -- pending | rejected | approved | revoked | expired
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

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

            -- معلومات كل اختبار: العنوان، وإذا انحذف (الرقم ما بيرجع ينستخدم)
            CREATE TABLE IF NOT EXISTS exams (
                subject TEXT NOT NULL,
                num INTEGER NOT NULL,
                title TEXT,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (subject, num)
            );

            -- أرقام البطاقات المسموحة للقبول التلقائي
            CREATE TABLE IF NOT EXISTS allowed_cards (
                subject TEXT NOT NULL,
                card TEXT NOT NULL,
                PRIMARY KEY (subject, card)
            );

            -- إعدادات كل مادة (المدة الافتراضية، الإشعارات...)
            CREATE TABLE IF NOT EXISTS settings (
                subject TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (subject, key)
            );

            -- صفحات الملفات بعد تحويلها لصور (file_id لكل صفحة)، لحتى التحويل يصير مرة وحدة
            CREATE TABLE IF NOT EXISTS page_cache (
                cache_key TEXT NOT NULL,
                idx INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                PRIMARY KEY (cache_key, idx)
            );

            -- (قديم، ما عاد مستخدم) نسخ الحلول المعلّمة
            CREATE TABLE IF NOT EXISTS watermarks (
                subject TEXT NOT NULL,
                exam_num INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                source_file_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                file_type TEXT NOT NULL,
                PRIMARY KEY (subject, exam_num, user_id)
            );
            """
        )
        # ترقية قاعدة بيانات النسخ القديمة بدون ما ينمسح شي
        _add_column(conn, "requests", "expires_at", "TEXT")
        _add_column(conn, "requests", "reminded", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "requests", "renew_requested_at", "TEXT")
        _add_column(conn, "attempts", "score", "INTEGER")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_req_user ON requests(user_id);
            CREATE INDEX IF NOT EXISTS idx_req_subject ON requests(subject, status);
            CREATE INDEX IF NOT EXISTS idx_req_card ON requests(subject, card_number);
            """
        )
        conn.execute("UPDATE attempts SET status = 'exam_sent' WHERE status IN ('submitted', 'answer_rejected')")
        # اختبارات محذوفة بالنسخة القديمة (كانت بتضل محجوزة): منظّفها لحتى أرقامها ترجع تنستخدم
        for row in conn.execute("SELECT subject, num FROM exams WHERE deleted = 1").fetchall():
            if not conn.execute("SELECT 1 FROM files WHERE subject = ? AND exam_num = ? AND kind = 'exam'",
                                (row["subject"], row["num"])).fetchone():
                _purge_exam(conn, row["subject"], row["num"])
        # كل اختبار مرفوع من قبل بيصيرلو سطر بجدول exams
        conn.execute(
            "INSERT OR IGNORE INTO exams(subject, num, created_at)"
            " SELECT subject, exam_num, ? FROM files WHERE kind = 'exam'",
            (now(),),
        )


# ---------------------------------------------------------------- الوقت
def now() -> str:
    """الوقت بتوقيت سوريا (مو توقيت السيرفر)."""
    return datetime.now(TZ).strftime(TIME_FMT)


def after_days(days: int, base: str | None = None) -> str:
    start = datetime.now(TZ)
    if base:
        try:
            b = datetime.strptime(base, TIME_FMT).replace(tzinfo=TZ)
            start = max(start, b)
        except ValueError:
            pass
    return (start + timedelta(days=days)).strftime(TIME_FMT)


def minutes_since(stamp: str) -> float:
    try:
        then = datetime.strptime(stamp, TIME_FMT).replace(tzinfo=TZ)
    except (TypeError, ValueError):
        return 10 ** 9
    return (datetime.now(TZ) - then).total_seconds() / 60


def days_left(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    try:
        end = datetime.strptime(expires_at, TIME_FMT).replace(tzinfo=TZ)
    except ValueError:
        return None
    return max(0, (end - datetime.now(TZ)).days)


# ---------------------------------------------------------------- أدوات عامة
def q_one(sql: str, args=()):
    with closing(db()) as conn:
        return conn.execute(sql, args).fetchone()


def q_all(sql: str, args=()):
    with closing(db()) as conn:
        return conn.execute(sql, args).fetchall()


def execute(sql: str, args=()) -> int:
    with closing(db()) as conn, conn:
        return conn.execute(sql, args).lastrowid


def execute_count(sql: str, args=()) -> int:
    """بيرجّع عدد الصفوف يلي تغيّرت (للتحديثات يلي لازم تصير مرة وحدة بس)."""
    with closing(db()) as conn, conn:
        return conn.execute(sql, args).rowcount


# ---------------------------------------------------------------- الطلبات والاشتراكات
def get_request(req_id: int):
    return q_one("SELECT * FROM requests WHERE id = ?", (req_id,))


def user_requests(user_id: int):
    return q_all("SELECT * FROM requests WHERE user_id = ? ORDER BY id DESC", (user_id,))


def is_active(req) -> bool:
    """الطالب مقبول واشتراكه ما خلص."""
    return bool(req) and req["status"] == "approved" and (not req["expires_at"] or req["expires_at"] > now())


def set_request_status(req_id: int, new_status: str, only_from: tuple[str, ...]) -> bool:
    """بيغيّر حالة الطلب بس إذا كانت حالته الحالية من only_from. بيرجّع True إذا تغيّرت."""
    marks = ",".join("?" * len(only_from))
    return execute_count(
        f"UPDATE requests SET status = ?, updated_at = ? WHERE id = ? AND status IN ({marks})",
        (new_status, now(), req_id, *only_from),
    ) == 1


def approve_request(req_id: int, days: int, only_from: tuple[str, ...]) -> bool:
    """قبول/تجديد: days = 0 يعني بدون انتهاء. التجديد بيبلّش من آخر الاشتراك الحالي إذا لسّا ما خلص."""
    req = get_request(req_id)
    if not req or req["status"] not in only_from:
        return False
    base = req["expires_at"] if req["status"] == "approved" else None
    expires = after_days(days, base) if days else None
    marks = ",".join("?" * len(only_from))
    return execute_count(
        f"UPDATE requests SET status = 'approved', expires_at = ?, reminded = 0, updated_at = ?"
        f" WHERE id = ? AND status IN ({marks})",
        (expires, now(), req_id, *only_from),
    ) == 1


def same_card_requests(subject: str, card: str, exclude_user: int):
    """طلبات تانية بنفس رقم البطاقة بنفس المادة، من حسابات تانية (مشاركة اشتراك محتملة)."""
    return q_all(
        "SELECT id, full_name, user_id, status FROM requests"
        " WHERE subject = ? AND card_number = ? AND user_id != ? AND status != 'rejected'",
        (subject, card, exclude_user),
    )


def active_students(subject: str):
    return q_all(
        f"SELECT r.id, r.user_id FROM requests r WHERE r.subject = ? AND {ACTIVE_SQL}", (subject, now())
    )


# ---------------------------------------------------------------- المحاولات
def get_attempt(att_id: int):
    """المحاولة مع معلومات الطالب والمادة."""
    return q_one(
        "SELECT a.*, r.user_id, r.subject, r.status AS req_status, r.expires_at, r.full_name, r.card_number"
        " FROM attempts a JOIN requests r ON r.id = a.request_id WHERE a.id = ?",
        (att_id,),
    )


def find_attempt(req_id: int, exam_num: int):
    return q_one("SELECT * FROM attempts WHERE request_id = ? AND exam_num = ?", (req_id, exam_num))


def set_attempt(att_id: int, status: str) -> None:
    execute("UPDATE attempts SET status = ?, updated_at = ? WHERE id = ?", (status, now(), att_id))


def move_attempt(att_id: int, new_status: str, from_status: str) -> bool:
    """تغيير ذرّي: بينجح مرة وحدة بس حتى لو الطالب ضغط الزر مرتين."""
    return execute_count(
        "UPDATE attempts SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
        (new_status, now(), att_id, from_status),
    ) == 1


def set_score(att_id: int, score: int) -> None:
    execute("UPDATE attempts SET score = ?, updated_at = ? WHERE id = ?", (score, now(), att_id))


# ---------------------------------------------------------------- الاختبارات والملفات
def get_file(subject: str, exam_num: int, kind: str):
    return q_one(
        "SELECT * FROM files WHERE subject = ? AND exam_num = ? AND kind = ?", (subject, exam_num, kind)
    )


def save_file(subject: str, num: int, kind: str, file_id: str, ftype: str) -> None:
    execute(
        "INSERT OR REPLACE INTO files(subject, exam_num, kind, file_id, file_type) VALUES (?, ?, ?, ?, ?)",
        (subject, num, kind, file_id, ftype),
    )
    if kind == "exam":
        execute("INSERT OR IGNORE INTO exams(subject, num, created_at) VALUES (?, ?, ?)", (subject, num, now()))
    else:  # الحلول تغيّرت: النسخ المعلّمة القديمة ما عادت صالحة
        execute("DELETE FROM watermarks WHERE subject = ? AND exam_num = ?", (subject, num))


def uploaded_nums(subject: str, kind: str) -> list[int]:
    return [r["exam_num"] for r in q_all(
        "SELECT exam_num FROM files WHERE subject = ? AND kind = ? ORDER BY exam_num", (subject, kind))]


def next_exam_num(subject: str) -> int:
    """أصغر رقم فاضي: إذا انحذف اختبار، رقمه بيرجع ينستخدم (1، 2، 3 ← حذف 3 ← الجاي 3)."""
    used = set(uploaded_nums(subject, "exam"))
    n = 1
    while n in used:
        n += 1
    return n


def exam_titles(subject: str) -> dict[int, str]:
    return {r["num"]: r["title"] for r in q_all(
        "SELECT num, title FROM exams WHERE subject = ? AND title IS NOT NULL AND title != ''", (subject,))}


def exam_label(subject: str, num: int, titles: dict[int, str] | None = None) -> str:
    title = (titles if titles is not None else exam_titles(subject)).get(num)
    return f"اختبار {num}: {title}" if title else f"اختبار {num}"


def set_exam_title(subject: str, num: int, title: str | None) -> None:
    execute("INSERT OR IGNORE INTO exams(subject, num, created_at) VALUES (?, ?, ?)", (subject, num, now()))
    execute("UPDATE exams SET title = ? WHERE subject = ? AND num = ?", (title, subject, num))


def delete_exam(subject: str, num: int) -> None:
    """بيحذف الاختبار كامل: ملفاته، عنوانه، ومحاولات الطلاب ودرجاتهم فيه.
    هيك لما ينرفع اختبار جديد بنفس الرقم، بيبلّش نضيف عند كل الطلاب."""
    with closing(db()) as conn, conn:
        _purge_exam(conn, subject, num)


def _purge_exam(conn, subject: str, num: int) -> None:
    conn.execute("DELETE FROM files WHERE subject = ? AND exam_num = ?", (subject, num))
    conn.execute("DELETE FROM page_cache WHERE cache_key LIKE ?", (f"%:{subject}:{num}:%",))
    conn.execute("DELETE FROM watermarks WHERE subject = ? AND exam_num = ?", (subject, num))
    conn.execute("DELETE FROM exams WHERE subject = ? AND num = ?", (subject, num))
    conn.execute(
        "DELETE FROM attempts WHERE exam_num = ? AND request_id IN (SELECT id FROM requests WHERE subject = ?)",
        (num, subject),
    )


# ---------------------------------------------------------------- الإعدادات والبطاقات المسموحة
def get_setting(subject: str, key: str, default: str) -> str:
    row = q_one("SELECT value FROM settings WHERE subject = ? AND key = ?", (subject, key))
    return row["value"] if row else default


def set_setting(subject: str, key: str, value: str) -> None:
    execute("INSERT OR REPLACE INTO settings(subject, key, value) VALUES (?, ?, ?)", (subject, key, value))


def default_days(subject: str) -> int:
    return int(get_setting(subject, "default_days", "0"))


def notify_new_exams(subject: str) -> bool:
    return get_setting(subject, "notify_new", "1") == "1"


def add_allowed_cards(subject: str, cards: list[str]) -> int:
    with closing(db()) as conn, conn:
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO allowed_cards(subject, card) VALUES (?, ?)",
                         [(subject, c) for c in cards])
        return conn.total_changes - before


def is_allowed_card(subject: str, card: str) -> bool:
    return q_one("SELECT 1 FROM allowed_cards WHERE subject = ? AND card = ?", (subject, card)) is not None


def allowed_cards(subject: str) -> list[str]:
    return [r["card"] for r in q_all("SELECT card FROM allowed_cards WHERE subject = ? ORDER BY card", (subject,))]


def clear_allowed_cards(subject: str) -> int:
    return execute_count("DELETE FROM allowed_cards WHERE subject = ?", (subject,))


# ---------------------------------------------------------------- العلامة المائية (كاش)
def get_watermark(subject: str, num: int, user_id: int, source_file_id: str):
    return q_one(
        "SELECT * FROM watermarks WHERE subject = ? AND exam_num = ? AND user_id = ? AND source_file_id = ?",
        (subject, num, user_id, source_file_id),
    )


def save_watermark(subject: str, num: int, user_id: int, source_file_id: str, file_id: str, ftype: str) -> None:
    execute(
        "INSERT OR REPLACE INTO watermarks(subject, exam_num, user_id, source_file_id, file_id, file_type)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (subject, num, user_id, source_file_id, file_id, ftype),
    )


# ---------------------------------------------------------------- صفحات الملفات كصور (كاش)
def get_pages(cache_key: str) -> list[str]:
    return [r["file_id"] for r in q_all("SELECT file_id FROM page_cache WHERE cache_key = ? ORDER BY idx", (cache_key,))]


def save_pages(cache_key: str, file_ids: list[str]) -> None:
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM page_cache WHERE cache_key = ?", (cache_key,))
        conn.executemany("INSERT INTO page_cache(cache_key, idx, file_id) VALUES (?, ?, ?)",
                         [(cache_key, i, fid) for i, fid in enumerate(file_ids)])
