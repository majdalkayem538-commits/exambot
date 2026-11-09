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

from telegram import InputFile, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import WATERMARK, log
from database import get_pages, save_pages
from utils import send_stored_file

MAX_BYTES = 20 * 1024 * 1024  # أكبر ملف بيقدر البوت ينزّله من تلغرام
MAX_PAGES = 60                # أكتر من هيك بينبعت كملف
SCALE = 2.0                   # دقة الصفحة (A4 ← تقريباً 1190×1684)
ALBUM = 10                    # حد تلغرام للصور بالألبوم الواحد


def _to_jpeg(img) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def render_pdf(data: bytes, name: str | None = None, card: str | None = None) -> list[bytes]:
    """كل صفحة من الـ PDF ← صورة JPEG (مع العلامة المائية إذا في اسم)."""
    import pypdfium2 as pdfium
    from watermark import stamp_pil

    pdf = pdfium.PdfDocument(data)
    try:
        if len(pdf) > MAX_PAGES:
            raise ValueError(f"too many pages ({len(pdf)})")
        pages = []
        for i in range(len(pdf)):
            img = pdf[i].render(scale=SCALE).to_pil()
            if name:
                img = stamp_pil(img, name, card or "")
            pages.append(_to_jpeg(img))
        return pages
    finally:
        pdf.close()


def render_image(data: bytes, name: str | None = None, card: str | None = None) -> bytes:
    from PIL import Image
    from watermark import stamp_pil

    img = Image.open(io.BytesIO(data))
    return _to_jpeg(stamp_pil(img, name, card or "") if name else img)


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

        def media(x, i):
            src = InputFile(io.BytesIO(x), filename=f"page_{start + i + 1}.jpg") if isinstance(x, bytes) else x
            return src

        if len(chunk) == 1:
            msg = await bot.send_photo(chat_id, media(chunk[0], 0), caption=first_cap)
            ids.append(msg.photo[-1].file_id)
            continue
        group = [
            InputMediaPhoto(media(x, i), caption=first_cap if i == 0 else None, parse_mode=ParseMode.HTML)
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
        await send_album(bot, chat_id, cached, caption)
        return "images"
    try:
        data = await download(bot, f["file_id"])
        if data[:5] == b"%PDF-":
            images = await asyncio.to_thread(render_pdf, data, name, card)
        elif f["file_type"] == "photo" or data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
            images = [await asyncio.to_thread(render_image, data, name, card)]
        else:
            raise ValueError("not a PDF or image")
    except Exception as err:  # أي مشكلة بالتحويل: منبعت الملف الأصلي (محمي) بدل ما يوقف الطالب
        log.warning("convert failed for %s: %s — sending original", cache_key, err)
        await send_stored_file(bot, chat_id, f, caption)
        return "original"
    ids = await send_album(bot, chat_id, images, caption)
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
