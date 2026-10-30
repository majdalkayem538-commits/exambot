"""قاعدة البيانات (SQLite): الطلبات، محاولات الاختبارات، والملفات."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime

from config import DB_PATH


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
