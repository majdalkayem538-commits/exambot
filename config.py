"""الإعدادات: بتنقرا كلها من الـ Environment.

مطلوبة:
  BOT_TOKEN            توكن البوت من @BotFather
  SUBJECT_1_ADMIN_ID   الـ ID الرقمي للآنسة سمارا (من @userinfobot)
  SUBJECT_2_ADMIN_ID   الـ ID الرقمي للآنسة زينب

اختيارية (إلها قيم افتراضية):
  SUBJECT_1_NAME / SUBJECT_1_TEACHER   (التحليل الكلي / سمارا)
  SUBJECT_2_NAME / SUBJECT_2_TEACHER   (الرياضيات الاقتصادية / زينب)
  DB_PATH                              (exam_bot.db — على Render: /var/data/exam_bot.db)
  OWNER_ID                             الـ ID تبعك كصاحب البوت: بتشوف إحصائيات كل المواد
  TIMEZONE                             (Asia/Damascus) — لحتى الأوقات تطلع بتوقيت سوريا مو توقيت السيرفر
  MIN_SOLVE_MINUTES                    (0) — أقل وقت (بالدقايق) بين فتح الاختبار وزر «لقد أنهيت الحل»
  BACKUP_HOUR                          (3) — ساعة النسخة الاحتياطية اليومية (بتنبعت لـ OWNER_ID)
  WATERMARK                            (1) — 1 = علامة مائية باسم الطالب على ملفات الحلول، 0 = بدون
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta, timezone, tzinfo

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
# مهم: httpx بيطبع روابط فيها التوكن، فمنخفّي سجلّاتو
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("exam_bot")


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        sys.exit(f"⚠️ المتغير {name} مو موجود بالـ Environment. ضيفه وأعد التشغيل.")
    return value


def env_int(name: str, default: int | None = None, minimum: int | None = None) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        if default is None:
            sys.exit(f"⚠️ المتغير {name} مو موجود بالـ Environment. ضيفه وأعد التشغيل.")
        return default
    if not value.lstrip("-").isdigit():
        sys.exit(f"⚠️ المتغير {name} لازم يكون رقم، مو '{value}'.")
    number = int(value)
    if minimum is not None and number < minimum:
        sys.exit(f"⚠️ المتغير {name} لازم يكون {minimum} أو أكتر.")
    return number


def load_timezone(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # إذا ما في بيانات المناطق الزمنية على السيرفر
        log.warning("timezone %s not available, using UTC+3", name)
        return timezone(timedelta(hours=3))


BOT_TOKEN = env_required("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "exam_bot.db").strip() or "exam_bot.db"
TZ = load_timezone(os.getenv("TIMEZONE", "Asia/Damascus").strip() or "Asia/Damascus")
MIN_SOLVE_MINUTES = env_int("MIN_SOLVE_MINUTES", default=0, minimum=0)
BACKUP_HOUR = env_int("BACKUP_HOUR", default=3, minimum=0) % 24
WATERMARK = env_int("WATERMARK", default=1, minimum=0) == 1

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

# صاحب البوت (اختياري): بيشوف إحصائيات كل المواد ومجموع الطلاب
OWNER_ID = env_int("OWNER_ID", default=0)

ADMIN_SUBJECTS: dict[int, list[str]] = {}
for key, s in SUBJECTS.items():
    ADMIN_SUBJECTS.setdefault(s["admin"], []).append(key)
ADMIN_IDS = set(ADMIN_SUBJECTS)

REQ_LABELS = {
    "pending": "⏳ بانتظار موافقة الآنسة",
    "rejected": "❌ مرفوض",
    "approved": "✅ مقبول",
    "revoked": "🚫 موقوف من الآنسة",
    "expired": "⌛ منتهي الاشتراك",
}
# نفس الحالات بالفصحى (للطالب)
REQ_LABELS_STUDENT = {
    "pending": "⏳ بانتظار موافقة الآنسة",
    "rejected": "❌ مرفوض",
    "approved": "✅ مقبول",
    "revoked": "🚫 موقوف",
    "expired": "⌛ انتهى الاشتراك",
}
# حالات الاختبار عند الطالب: بدون محاولة ▫️ | exam_sent 📝 | waiting ⏳ (خلص وبينطر الحلول) | done ✅
ATT_ICONS = {None: "▫️", "exam_sent": "📝", "waiting": "⏳", "done": "✅"}
PAGE_STUDENT = 20  # عدد الاختبارات بكل صفحة عند الطالب
PAGE_ADMIN = 25  # عدد الأرقام بكل صفحة عند الآنسة

# حدود الحماية
NAME_MAX_LEN = 60          # أطول اسم مقبول (حرف)
INFO_MAX_LEN = 200         # أطول رسالة تسجيل
UPLOAD_TIMEOUT_MIN = 15    # بعد كم دقيقة بينلغى اختيار رقم الملف إذا ما انبعت ملف
FLOOD_LIMIT = 12           # أكتر عدد رسائل/ضغطات مسموح...
FLOOD_WINDOW_SEC = 10      # ...خلال هالعدد من الثواني
REMIND_DAYS = 3            # تذكير الطالب قبل انتهاء الاشتراك بكم يوم
RENEW_COOLDOWN_HOURS = 12  # أقل مدة بين طلبين تجديد من نفس الطالب
TITLE_MAX_LEN = 60         # أطول عنوان اختبار
# مدد الاشتراك يلي بتطلع للآنسة كأزرار (بالأيام، 0 = بدون انتهاء)
DURATION_CHOICES = [(30, "شهر"), (60, "شهرين"), (90, "3 أشهر"), (120, "4 أشهر (فصل)"),
                    (180, "6 أشهر"), (365, "سنة"), (0, "♾️ بدون انتهاء")]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "fonts", "DejaVuSans-Bold.ttf")


def duration_label(days: int) -> str:
    for d, label in DURATION_CHOICES:
        if d == days:
            return label
    return f"{days} يوم"


def subject_name(key: str) -> str:
    return SUBJECTS[key]["name"]


def teacher_title(key: str) -> str:
    t = SUBJECTS[key]["teacher"]
    return f"الآنسة {t}" if t else "الآنسة"


def can_manage(user_id: int, subject: str) -> bool:
    """مين بيقدر يتحكم بطلاب المادة: آنستها، أو صاحب البوت."""
    return subject in SUBJECTS and (SUBJECTS[subject]["admin"] == user_id or (OWNER_ID and user_id == OWNER_ID))
