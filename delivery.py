"""إرسال الاختبارات والحلول للطالب كصور محمية.

ليش صور؟ ملف PDF على الموبايل بينفتح بتطبيق قارئ PDF خارجي، ومن هونيك الطالب
بيقدر يشاركه لأي مكان، وحماية تلغرام ما بتوصل لبرّا. الصور بتنفتح جوّا تلغرام بس،
فالحماية (منع الحفظ والتحويل، ومنع لقطة الشاشة على أندرويد) بتضل شغّالة.

- PDF ← كل صفحة صورة، بتنبعت كألبوم (10 صفحات بكل ألبوم).
- الحلول ← على كل صفحة اسم الطالب ورقم بطاقته (إذا WATERMARK=1).
- الصور يلي بيرفعها البوت بتنحفظ (كاش)، فالتحويل بيصير مرة وحدة بس لكل ملف.
- أي ملف ما بينفع يتحوّل (Word مثلاً) بينبعت متل ما هو، محمي بس ممكن ينفتح ببرنامج خارجي.
"""

from __future__ import annotations

import asyncio
import io

from telegram import InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from config import WATERMARK, log
from database import get_pages, save_pages
from utils import send_stored_file

MAX_BYTES = 20 * 1024 * 1024  # أكبر ملف بيقدر البوت ينزّله من تلغرام
MAX_PAGES = 60                # أكتر من هيك بينبعت كملف
SCALE = 2.0                   # أعلى تكبير للصفحات الصغيرة (A4 ← تقريباً 1190×1684)
LONG_SIDE = 2048              # أطول ضلع للصورة: تلغرام بيرفض الصور الكبيرة (العرض + الطول ≤ 10000)
MAX_RATIO = 19                # تلغرام بيرفض الصور الطويلة كتير (نسبة > 20)
ALBUM = 10                    # حد تلغرام للصور بالألبوم الواحد


def _fit(img):
    """بيصغّر الصورة لحتى تقبلها تلغرام كصورة."""
    w, h = img.size
    if max(w, h) > LONG_SIDE:
        r = LONG_SIDE / max(w, h)
        img = img.resize((max(1, int(w * r)), max(1, int(h * r))))
    w, h = img.size
    if max(w, h) / max(1, min(w, h)) > MAX_RATIO:
        raise ValueError(f"page too long/narrow ({w}x{h})")
    return img


def _to_jpeg(img) -> bytes:
    img = _fit(img)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def _safe_stamp(img, name: str, card: str | None):
    """العلامة المائية. إذا فشلت (مثلاً الخط ناقص)، منكمّل بالصورة بدونها: صورة محمية أأمن من PDF."""
    from watermark import stamp_pil
    try:
        return stamp_pil(img, name, card or "")
    except Exception as err:
        log.error("watermark failed, sending page without it: %s", err)
        return img


def self_check() -> list[str]:
    """فحص وقت التشغيل: هل كل شي لازم للتحويل والعلامة المائية موجود؟"""
    import os
    from config import FONT_PATH
    problems = []
    import importlib.util
    if importlib.util.find_spec("pypdfium2") is None:
        problems.append("مكتبة pypdfium2 مو منزّلة (requirements.txt قديم؟) — الـ PDF رح ينبعت كملف")
    if importlib.util.find_spec("PIL") is None:
        problems.append("مكتبة Pillow مو منزّلة")
    if not os.path.exists(FONT_PATH):
        problems.append(f"الخط مو موجود ({FONT_PATH}) — ارفع مجلد fonts على GitHub. الحلول رح تنبعت بدون علامة مائية")
    return problems


def render_pdf(data: bytes, name: str | None = None, card: str | None = None) -> list[bytes]:
    """كل صفحة من الـ PDF ← صورة JPEG (مع العلامة المائية إذا في اسم)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        if len(pdf) > MAX_PAGES:
            raise ValueError(f"too many pages ({len(pdf)})")
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            pw, ph = page.get_size()  # بالـ points
            scale = min(SCALE, LONG_SIDE / max(pw, ph, 1))  # صفحات السكانر الكبيرة بتنصغّر بدل ما تتكبّر
            img = page.render(scale=scale).to_pil()
            if name:
                img = _safe_stamp(img, name, card)
            pages.append(_to_jpeg(img))
        return pages
    finally:
        pdf.close()


def render_image(data: bytes, name: str | None = None, card: str | None = None) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    return _to_jpeg(_safe_stamp(img, name, card) if name else img)


def pdf_page_count(data: bytes) -> int:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


async def download(bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    if tg_file.file_size and tg_file.file_size > MAX_BYTES:
        raise ValueError("file too large")
    return bytes(await tg_file.download_as_bytearray())


async def send_album(bot, chat_id: int, items: list, caption: str) -> list[str]:
    """بيبعت صور (bytes أو file_id) كألبومات، والتعليق على أول صورة. بيرجّع file_id لكل صورة."""
    ids: list[str] = []
    for start in range(0, len(items), ALBUM):
        chunk = items[start:start + ALBUM]
        first_cap = caption if start == 0 else None

        if len(chunk) == 1:
            x = chunk[0]
            if isinstance(x, bytes):
                msg = await bot.send_photo(chat_id, x, caption=first_cap, filename=f"page_{start + 1}.jpg")
            else:
                msg = await bot.send_photo(chat_id, x, caption=first_cap)
            ids.append(msg.photo[-1].file_id)
            continue
        # مهم: الصور الجديدة لازم تنبعت كـ bytes مع filename، لحتى المكتبة ترفقها بالطلب (attach://)
        group = [
            InputMediaPhoto(x, caption=first_cap if i == 0 else None, parse_mode=ParseMode.HTML,
                            filename=f"page_{start + i + 1}.jpg" if isinstance(x, bytes) else None)
            for i, x in enumerate(chunk)
        ]
        msgs = await bot.send_media_group(chat_id, group)
        ids += [m.photo[-1].file_id for m in msgs]
    return ids


async def deliver(bot, chat_id: int, f, cache_key: str, caption: str,
                  name: str | None = None, card: str | None = None) -> str:
    """بيبعت ملف مرفوع كصور محمية. بيرجّع "images" أو "original" (إذا بعته متل ما هو)."""
    if f["file_type"] == "video":
        await send_stored_file(bot, chat_id, f, caption)
        return "original"
    cached = get_pages(cache_key)
    if cached:
        try:
            await send_album(bot, chat_id, cached, caption)
            return "images"
        except BadRequest as err:  # صور الكاش ما عادت صالحة: منعيد التحويل
            log.warning("cached pages rejected for %s: %s — rebuilding", cache_key, err)
            save_pages(cache_key, [])
    try:
        data = await download(bot, f["file_id"])
        if data[:5] == b"%PDF-":
            images = await asyncio.to_thread(render_pdf, data, name, card)
        elif f["file_type"] == "photo" or data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
            images = [await asyncio.to_thread(render_image, data, name, card)]
        else:
            raise ValueError("not a PDF or image")
    except Exception as err:  # أي مشكلة بالتحويل: منبعت الملف الأصلي (محمي) بدل ما يوقف الطالب
        log.error("convert failed for %s: %r — sending original PDF", cache_key, err)
        await send_stored_file(bot, chat_id, f, caption)
        return "original"
    try:
        ids = await send_album(bot, chat_id, images, caption)
    except BadRequest as err:
        log.warning("telegram rejected pages for %s: %s — sending original", cache_key, err)
        await send_stored_file(bot, chat_id, f, caption)
        return "original"
    save_pages(cache_key, ids)
    return "images"


def exam_key(subject: str, num: int, f) -> str:
    return f"exam:{subject}:{num}:{f['file_id']}"


def solution_key(subject: str, num: int, user_id: int, f) -> str:
    return f"sol:{subject}:{num}:{user_id if WATERMARK else 0}:{f['file_id']}"


async def send_exam(bot, chat_id: int, subject: str, num: int, f, caption: str) -> str:
    return await deliver(bot, chat_id, f, exam_key(subject, num, f), caption)


async def send_solution(bot, chat_id: int, subject: str, num: int, sol, name: str, card: str, caption: str) -> str:
    """الحلول، وعلى كل صفحة اسم الطالب ورقم بطاقته."""
    if WATERMARK:
        return await deliver(bot, chat_id, sol, solution_key(subject, num, chat_id, sol), caption, name, card)
    return await deliver(bot, chat_id, sol, solution_key(subject, num, chat_id, sol), caption)


async def describe_upload(bot, f) -> str:
    """للآنسة بعد الرفع: كيف رح يوصل الملف للطلاب."""
    if f["file_type"] in ("photo", "video"):
        return ""
    try:
        data = await download(bot, f["file_id"])
    except (TelegramError, ValueError) as err:
        return f"\n⚠️ ما قدرت افحص الملف ({err})."
    if data[:5] == b"%PDF-":
        try:
            n = await asyncio.to_thread(pdf_page_count, data)
        except Exception:
            return "\n⚠️ ملف الـ PDF تالف أو محمي بكلمة سر، رح ينبعت متل ما هو."
        if n > MAX_PAGES:
            return f"\n⚠️ الملف فيه {n} صفحة (أكتر من {MAX_PAGES})، رح ينبعت كملف PDF والطالب ممكن يشاركه. قسّميه لملفات أصغر."
        return f"\n🔒 رح يوصل للطلاب كـ {n} صورة محمية (ما بينفتح ببرامج خارجية)."
    return ("\n⚠️ هاد الملف مو PDF، فرح ينبعت متل ما هو، والطالب ممكن يفتحه ببرنامج خارجي ويشاركه."
            "\nالأفضل ترفعيه PDF أو صور.")
