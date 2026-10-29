"""الإعدادات: بتنقرا كلها من الـ Environment.

مطلوبة:
  BOT_TOKEN            توكن البوت من @BotFather
  SUBJECT_1_ADMIN_ID   الـ ID الرقمي للآنسة سمارا (من @userinfobot)
  SUBJECT_2_ADMIN_ID   الـ ID الرقمي للآنسة زينب

اختيارية (إلها قيم افتراضية):
  SUBJECT_1_NAME / SUBJECT_1_TEACHER   (التحليل الكلي / سمارا)
  SUBJECT_2_NAME / SUBJECT_2_TEACHER   (الرياضيات الاقتصادية / زينب)
  DB_PATH                              (exam_bot.db — على Render: /var/data/exam_bot.db)
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)


logging.getLogger("httpx").setLevel(logging.WARNING)


log = logging.getLogger("exam_bot")


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        sys.exit(f"⚠️ المتغير {name} مو موجود بالـ Environment. ضيفه وأعد التشغيل.")
    return value


def env_int(name: str) -> int:
    value = env_required(name)
    if not value.lstrip("-").isdigit():
        sys.exit(f"⚠️ المتغير {name} لازم يكون رقم (الـ ID الرقمي من @userinfobot)، مو '{value}'.")
    return int(value)


BOT_TOKEN = env_required("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "exam_bot.db").strip() or "exam_bot.db"

SUBJECT_DEFAULTS = {
    "s1": ("التحليل الكلي", "سمارا"),
    "s2": ("الرياضيات الاقتصادية", "زينب"),
}
SUBJECTS: dict[str, dict] = {}
for i, (key, (default_name, default_teacher)) in enumerate(SUBJECT_DEFAULTS.items(), start=1):
    SUBJECTS[key] = {
        "name": os.getenv(f"SUBJECT_{i}_NAME", "").strip() or default_name,
        "teacher": os.getenv(f"SUBJECT_{i}_TEACHER", "").strip() or default_teacher,
        "admin": env_int(f"SUBJECT_{i}_ADMIN_ID"),
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
PAGE_STUDENT = 20  # عدد الاختبارات بكل صفحة عند الطالب
PAGE_ADMIN = 25  # عدد الأرقام بكل صفحة عند الآنسة


def subject_name(key: str) -> str:
    return SUBJECTS[key]["name"]


def teacher_title(key: str) -> str:
    t = SUBJECTS[key]["teacher"]
    return f"الآنسة {t}" if t else "الآنسة"
