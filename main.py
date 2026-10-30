"""
بوت اختبارات تلغرام — مادتين، لكل مادة آنسة (أدمن)، وعدد غير محدود من الاختبارات، لكل اختبار ملف حلول.

الملفات:
  main.py          ← التشغيل وربط الأوامر والأزرار بالوظائف (هاد الملف)
  config.py        ← الإعدادات من الـ Environment
  database.py      ← قاعدة البيانات
  keyboards.py     ← كل الأزرار والقوائم
  utils.py         ← أدوات مساعدة
  common.py        ← /start وتوجيه الأزرار الثابتة
  registration.py  ← تسجيل الطالب
  student.py       ← شغل الطالب (الاختبارات وزر «لقد أنهيت الحل»)
  teacher.py       ← شغل الآنسة (القبول، الرفع)
  stats.py         ← الإحصائيات (كل آنسة بتشوف مقررها بس)

التشغيل:  python main.py
"""

from __future__ import annotations

import subprocess
import sys

# تنزيل المكتبة تلقائياً إذا مو موجودة
try:
    import telegram  # noqa: F401  (بس لنتأكد إنها منزّلة)
except ImportError:
    print("Installing python-telegram-bot ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot==21.*"])

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, Defaults, MessageHandler, filters

from common import fallback_text, menu_router, noop, start
from config import ADMIN_IDS, BOT_TOKEN, OWNER_ID, log
from database import init_db
from keyboards import MENU_FILTER
from registration import build_registration_handler
from stats import stats_callback, stats_cmd
from student import finish_back, finish_confirmed, finish_pressed, open_exam, show_menu, student_media
from teacher import (admin_decision, admin_file, pending_cmd, set_file_cmd, upload_number, upload_page,
                     upload_subject)


async def set_commands(app: Application) -> None:
    # زر "Menu" جنب مكان الكتابة
    await app.bot.set_my_commands([BotCommand("start", "القائمة الرئيسية")])


def main() -> None:
    if ":" not in BOT_TOKEN:
        sys.exit("⚠️ قيمة BOT_TOKEN مو صحيحة. انسخها كاملة من @BotFather.")
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
    stats_viewers = filters.User(user_id=list(ADMIN_IDS | ({OWNER_ID} if OWNER_ID else set())))

    app.add_handler(build_registration_handler())
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
    app.add_handler(CallbackQueryHandler(stats_callback, pattern=r"^(sto|sts:s\d|stc:s\d|st[le]:s\d:\d+|std:\d+:\d+)$"))
    app.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))

    app.add_handler(CommandHandler(["setexam", "setsolution"], set_file_cmd, filters=private & admins))
    app.add_handler(CommandHandler("pending", pending_cmd, filters=private & admins))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=private & stats_viewers))
    app.add_handler(MessageHandler(private & admins & (filters.Document.ALL | filters.PHOTO | filters.VIDEO), admin_file))

    app.add_handler(MessageHandler(private & ~admins & (filters.PHOTO | filters.Document.ALL | filters.VIDEO), student_media))
    app.add_handler(MessageHandler(private & MENU_FILTER, menu_router))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, fallback_text))

    log.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
