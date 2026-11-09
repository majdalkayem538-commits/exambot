"""
بوت اختبارات تلغرام — مادتين، لكل مادة آنسة (أدمن)، وعدد غير محدود من الاختبارات، لكل اختبار ملف حلول.

الملفات:
  main.py          ← التشغيل وربط الأزرار والأوامر بالوظائف، والحماية العامة (هاد الملف)
  config.py        ← الإعدادات من الـ Environment
  database.py      ← قاعدة البيانات
  keyboards.py     ← كل الأزرار والقوائم
  utils.py         ← أدوات مساعدة
  common.py        ← /start، توجيه الأزرار الثابتة، استقبال الردود
  registration.py  ← تسجيل الطالب (+ القبول التلقائي)
  student.py       ← شغل الطالب (الاختبارات، «لقد أنهيت الحل»، الدرجات، حالة الاشتراك)
  teacher.py       ← شغل الآنسة (القبول، رفع الملفات، العناوين، الحذف)
  subscription.py  ← مدة الاشتراك، التمديد، طلب التجديد، التذكير والإيقاف التلقائي
  settings.py      ← إعدادات الآنسة (المدة الافتراضية، البطاقات المسموحة، الإشعارات)
  broadcast.py     ← الرسائل الجماعية وإشعار الاختبار الجديد
  stats.py         ← الإحصائيات والبحث (كل آنسة بتشوف مقررها بس)
  backup.py        ← النسخ الاحتياطي اليومي والاسترجاع
  delivery.py      ← إرسال الاختبارات والحلول كصور محمية (صفحات الـ PDF ← صور)
  watermark.py     ← العلامة المائية باسم الطالب على ملفات الحلول
  fonts/           ← خط عربي للعلامة المائية

التشغيل:  python main.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

# تنزيل المكتبات تلقائياً إذا مو موجودة (للتشغيل على الجهاز)
if any(importlib.util.find_spec(m) is None for m in ("telegram", "apscheduler", "aiolimiter", "pypdfium2")):
    print("Installing requirements ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

import time
from collections import defaultdict, deque
from datetime import time as dtime

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (AIORateLimiter, Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, Defaults, MessageHandler, TypeHandler, filters)

from backup import backup_cmd, daily_backup_job, restore_callback
from broadcast import broadcast_callback
from common import input_router, menu_router, noop, start
from config import ADMIN_IDS, BACKUP_HOUR, BOT_TOKEN, FLOOD_LIMIT, FLOOD_WINDOW_SEC, OWNER_ID, TZ, log
from database import init_db
from keyboards import MENU_FILTER
from registration import build_registration_handler, quick_register
from settings import settings_callback
from stats import stats_callback, stats_cmd
from student import finish_back, finish_confirmed, finish_pressed, open_exam, score_prompt, show_menu
from subscription import (approve_back, approve_with_days, custom_days_prompt, extend_menu, extend_with_days,
                          renew_request, subscription_job)
from teacher import (admin_decision, exam_tools_callback, pending_cmd, set_file_cmd, upload_number, upload_page,
                     upload_subject)

N = r"\d{1,9}"   # أرقام الـ IDs بالأزرار: محدودة لحتى رقم مزوّر ضخم ما يعمل خطأ
D = r"\d{1,4}"   # عدد الأيام
S = r"s\d"       # المادة
STAFF = ADMIN_IDS | ({OWNER_ID} if OWNER_ID else set())
_recent: dict[int, deque] = defaultdict(deque)
_warned: dict[int, float] = {}


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بيشتغل قبل كل شي: البوت بس للمحادثات الخاصة، ومنع الإغراق بالرسائل."""
    chat = update.effective_chat
    if update.my_chat_member:
        # حدا ضاف البوت لمجموعة أو قناة: بيطلع منها
        if chat and chat.type != "private" and update.my_chat_member.new_chat_member.status in ("member", "administrator"):
            try:
                await context.bot.leave_chat(chat.id)
            except TelegramError:
                pass
        raise ApplicationHandlerStop
    if chat is None or chat.type != "private" or update.effective_user is None:
        raise ApplicationHandlerStop
    if update.message is None and update.callback_query is None:
        raise ApplicationHandlerStop  # رسائل معدّلة وغيرها: منتجاهلها

    uid = update.effective_user.id
    if uid in STAFF:
        return
    now_ = time.monotonic()
    q = _recent[uid]
    q.append(now_)
    while q and now_ - q[0] > FLOOD_WINDOW_SEC:
        q.popleft()
    if len(q) > FLOOD_LIMIT:
        if now_ - _warned.get(uid, 0) > 60:
            _warned[uid] = now_
            try:
                await context.bot.send_message(uid, "⏳ أرسلت رسائل كثيرة بسرعة. يُرجى الانتظار قليلًا ثم المحاولة مجددًا.")
            except TelegramError:
                pass
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except TelegramError:
                pass
        raise ApplicationHandlerStop


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أي خطأ ما انتظرناه: منسجّله، ومنخبّر المستخدم بدل ما يضل ناطر."""
    log.error("Unhandled error", exc_info=context.error)
    if isinstance(context.error, Forbidden) or not isinstance(update, Update) or not update.effective_user:
        return
    text = "حدث خطأ، يُرجى المحاولة بعد قليل 🙏"
    try:
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        else:
            await context.bot.send_message(update.effective_user.id, text)
    except TelegramError:
        pass


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand("start", "القائمة الرئيسية")])
    # مهام دورية: الاشتراكات كل 30 دقيقة، والنسخة الاحتياطية كل يوم
    app.job_queue.run_repeating(subscription_job, interval=1800, first=60, name="subscriptions")
    app.job_queue.run_daily(daily_backup_job, time=dtime(hour=BACKUP_HOUR, minute=0, tzinfo=TZ), name="backup")
    if not OWNER_ID:
        log.warning("OWNER_ID not set: daily backups are disabled")
    from delivery import self_check
    problems = self_check()
    for p in problems:
        log.error("SELF-CHECK: %s", p)
    if not problems:
        log.info("SELF-CHECK OK: PDF → images and watermark ready")
    elif OWNER_ID:
        try:
            await app.bot.send_message(OWNER_ID, "⚠️ فحص البوت:\n" + "\n".join(f"• {p}" for p in problems))
        except TelegramError:
            pass


def build_app() -> Application:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        # الحماية: كل رسالة/ملف من البوت ممنوع تحويله أو حفظه أو نسخه
        .defaults(Defaults(parse_mode=ParseMode.HTML, protect_content=True))
        # إذا تلغرام قال "استنى" (Flood wait)، البوت بيستنى وبيعيد الإرسال لحاله بدل ما يوقف
        .rate_limiter(AIORateLimiter(max_retries=3))
        # كذا طالب بنفس الوقت: ملف طالب كبير ما بيوقّف الباقيين
        .concurrent_updates(8)
        # رفع ألبومات الصور بياخد وقت
        .connect_timeout(20).read_timeout(30).write_timeout(30).media_write_timeout(120)
        .post_init(post_init)
        .build()
    )
    private = filters.ChatType.PRIVATE
    admins = filters.User(user_id=list(ADMIN_IDS))
    staff = filters.User(user_id=list(STAFF))

    def cb(func, pattern: str) -> None:
        app.add_handler(CallbackQueryHandler(func, pattern=pattern))

    app.add_handler(TypeHandler(Update, guard), group=-1)
    app.add_error_handler(on_error)
    app.add_handler(build_registration_handler())
    app.add_handler(CommandHandler("start", start, filters=private))

    # الطالب
    cb(show_menu, rf"^menu:{N}(:{N})?$")
    cb(open_exam, rf"^ex:{N}:{N}$")
    cb(finish_pressed, rf"^fin:{N}$")
    cb(finish_confirmed, rf"^fin_yes:{N}$")
    cb(finish_back, rf"^fin_no:{N}$")
    cb(score_prompt, rf"^sc:{N}$")
    cb(renew_request, rf"^renew:{N}$")
    cb(quick_register, rf"^quick:{S}$")
    # الآنسة: القبول والاشتراكات
    cb(admin_decision, rf"^(reg_ok|reg_no):{N}$")
    cb(approve_with_days, rf"^ap:{N}:{D}$")
    cb(approve_back, rf"^apb:{N}$")
    cb(extend_menu, rf"^extm:{N}:({N}|r)$")
    cb(extend_with_days, rf"^ext:{N}:{D}$")
    cb(custom_days_prompt, rf"^(apc|extc):{N}$|^sddc:{S}$")
    # الآنسة: الملفات
    cb(upload_subject, rf"^up:(exam|solution):{S}$")
    cb(upload_page, rf"^upg:(exam|solution):{S}:{N}$")
    cb(upload_number, rf"^upn:(exam|solution):{S}:({N}|new)$")
    cb(exam_tools_callback, rf"^(ttm|dlm):{S}:{N}$|^(ttl|dln|dly):{S}:{N}$")
    # الآنسة: الإعدادات والرسائل الجماعية
    cb(settings_callback, rf"^(cfgs|sdm|ntf|crd|cra|crx|crc|crcy):{S}$|^sdd:{S}:{D}$")
    cb(broadcast_callback, rf"^bcs:{S}$|^(bcy|bcn)$")
    # الإحصائيات
    cb(stats_callback, rf"^(sto|sts:{S}|stc:{S}|sfs:{S}|st[le]:{S}:{N}|(std|srv|srvy|srs):{N}:{N})$")
    # صاحب البوت
    cb(restore_callback, r"^(rsy|rsn)$")
    cb(noop, r"^noop$")

    app.add_handler(CommandHandler(["setexam", "setsolution"], set_file_cmd, filters=private & admins))
    app.add_handler(CommandHandler("pending", pending_cmd, filters=private & admins))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=private & staff))
    app.add_handler(CommandHandler("backup", backup_cmd, filters=private & staff))
    app.add_handler(MessageHandler(private & MENU_FILTER, menu_router))
    app.add_handler(MessageHandler(private & ~filters.COMMAND & (
        filters.TEXT | filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO
    ), input_router))
    return app


def main() -> None:
    if ":" not in BOT_TOKEN:
        sys.exit("⚠️ قيمة BOT_TOKEN مو صحيحة. انسخها كاملة من @BotFather.")
    init_db()
    app = build_app()
    log.info("Bot started")
    # بس الأنواع يلي البوت بيحتاجها (بدون الرسائل المعدّلة ومنشورات القنوات)
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER])


if __name__ == "__main__":
    main()
