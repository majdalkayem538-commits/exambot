"""العلامة المائية: بتكتب اسم الطالب ورقم بطاقته على كل صفحة من ملف الحلول (PDF أو صورة).

إذا الطالب نشر الملف أو صوّره، بيبيّن مين صاحبه. النسخة المعلّمة بتنحفظ (كاش)
لحتى ما تنعمل من جديد كل مرة بيطلب فيها الطالب الحلول.
"""

from __future__ import annotations

import io

from config import FONT_PATH

def _shape(text: str) -> str:
    """تجهيز النص العربي للرسم (وصل الحروف + الاتجاه من اليمين لليسار)."""
    import arabic_reshaper
    try:
        from bidi import get_display
    except ImportError:  # نسخ أقدم من python-bidi
        from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(text))


def _lines(name: str, card: str) -> tuple[str, str]:
    big = _shape(f"{name} — {card}")
    footer = _shape(f"نسخة خاصة بالطالب: {name} — رقم البطاقة: {card} — يمنع نشرها")
    return big, footer


# ---------------------------------------------------------------- PDF
def stamp_pdf(data: bytes, name: str, card: str) -> bytes:
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import Color
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    if "WM" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("WM", FONT_PATH))
    big, footer = _lines(name, card)
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter()
    overlays: dict[tuple[float, float], object] = {}
    for page in reader.pages:
        try:
            page.transfer_rotation_to_content()
        except Exception:
            pass
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        key = (round(w), round(h))
        if key not in overlays:
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            size = max(14, min(w, h) / 22)
            c.setFillColor(Color(0.55, 0.55, 0.55, alpha=0.18))
            c.setFont("WM", size)
            c.saveState()
            c.translate(w / 2, h / 2)
            c.rotate(35)
            step = size * 6
            for i in range(-3, 4):
                c.drawCentredString(0, i * step, big)
            c.restoreState()
            c.setFillColor(Color(0.35, 0.35, 0.35, alpha=0.75))
            c.setFont("WM", max(7, size * 0.45))
            c.drawCentredString(w / 2, 12, footer)
            c.save()
            overlays[key] = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
        page.merge_page(overlays[key])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------- الصور
def stamp_image(data: bytes, name: str, card: str) -> bytes:
    from PIL import Image
    img = stamp_pil(Image.open(io.BytesIO(data)), name, card)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


def stamp_pil(img, name: str, card: str):
    """بيرسم العلامة المائية على صورة PIL وبيرجّع صورة RGB."""
    from PIL import Image, ImageDraw, ImageFont, features

    if features.check("raqm"):  # Pillow بيرتّب العربي لحالو، فمنعطيه النص الأصلي
        big, footer = f"{name} — {card}", f"نسخة خاصة بالطالب: {name} — رقم البطاقة: {card} — يمنع نشرها"
    else:
        big, footer = _lines(name, card)
    img = img.convert("RGBA")
    w, h = img.size
    size = max(16, min(w, h) // 18)
    font = ImageFont.truetype(FONT_PATH, size)
    small = ImageFont.truetype(FONT_PATH, max(12, size // 2))

    # طبقة شفافة فيها الاسم مكرر بشكل مايل
    diag = int((w ** 2 + h ** 2) ** 0.5)
    layer = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tw = d.textlength(big, font=font)
    for y in range(0, diag, size * 5):
        d.text(((diag - tw) / 2, y), big, font=font, fill=(120, 120, 120, 60))
    layer = layer.rotate(35, resample=Image.BICUBIC)
    left, top = (diag - w) // 2, (diag - h) // 2
    img.alpha_composite(layer.crop((left, top, left + w, top + h)))

    d = ImageDraw.Draw(img)
    fw = d.textlength(footer, font=small)
    while fw > w * 0.94 and small.size > 8:  # السطر لازم يوسع بعرض الصفحة
        small = ImageFont.truetype(FONT_PATH, small.size - 1)
        fw = d.textlength(footer, font=small)
    d.rectangle((0, h - small.size * 2, w, h), fill=(255, 255, 255, 170))
    d.text(((w - fw) / 2, h - small.size * 1.6), footer, font=small, fill=(60, 60, 60, 230))
    return img.convert("RGB")


# الإرسال للطالب صار بملف delivery.py (صفحات PDF كصور محمية + العلامة المائية)
