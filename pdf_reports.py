# -*- coding: utf-8 -*-
"""
توليد تقارير PDF (السجل اليومي، فاتورة الحجز) بهوية بصرية تطابق قالب المنصة —
شريط علوي/سفلي داكن بالشعار، وخلفية كريمية، وخط ذهبي فاصل.

ملاحظة تقنية مهمة جدًا: مكتبة reportlab لا تشكّل الحروف العربية ولا تدعم اتجاه
الكتابة من اليمين لليسار تلقائيًا، لذلك كل نص عربي يُمرَّر عبر ar() قبل رسمه.
كذلك تبيّن أن كلاس Paragraph في reportlab يُجري معالجة يونيكود إضافية تتعارض
مع النص المُهيَّأ مسبقًا (تظهر مربعات فارغة "tofu" بين الكلمات) — لذلك هذا
الملف يرسم كل شيء يدويًا عبر canvas.drawRightString بدل Paragraph/Platypus،
مع التفاف نص يدوي وتقسيم صفحات يدوي عند الحاجة.
"""
import os
from datetime import datetime
from io import BytesIO

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
FONT_DIR = os.path.join(BASE_DIR, "static", "fonts")
LOGO_PATH = os.path.join(BASE_DIR, "static", "img", "logo.png")

# ---------------------------------------------------------------- الألوان (تطابق نظام التصميم في style.css)
INK = colors.HexColor("#14201A")
GOLD = colors.HexColor("#B08D4E")
GOLD_SOFT = colors.HexColor("#DFC28B")
PARCHMENT = colors.HexColor("#F1E9D8")
MUTED = colors.HexColor("#9C9480")
WHITE = colors.HexColor("#FFFFFF")
AMOUNT_BG = colors.HexColor("#F6F0E4")

PAGE_W, PAGE_H = A4
HEADER_H = 26 * mm
FOOTER_H = 14 * mm
MARGIN = 15 * mm
CONTENT_TOP = PAGE_H - HEADER_H - 12 * mm
CONTENT_BOTTOM = FOOTER_H + 12 * mm

_fonts_registered = False


def _register_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont("Amiri", os.path.join(FONT_DIR, "Amiri-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("Amiri-Bold", os.path.join(FONT_DIR, "Amiri-Bold.ttf")))
    _fonts_registered = True


def ar(text):
    """يهيئ نصًا عربيًا (أو مختلطًا) للعرض الصحيح في reportlab: تشكيل الحروف
    المتصلة + عكس اتجاه الكتابة للعرض المرئي الصحيح من اليمين لليسار."""
    if text is None:
        return ""
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)


def wrap_ar_lines(text, font, size, max_width):
    """يلف نصًا عربيًا (منطقيًا، قبل التهيئة) إلى عدة أسطر تناسب max_width،
    ويُرجع كل سطر بعد تهيئته للعرض. القياس يتم على النص المُهيَّأ (المرسوم فعليًا)."""
    text = (text or "").strip()
    if not text:
        return ["—"]
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(ar(candidate), font, size) <= max_width or not current:
            current = candidate
        else:
            lines.append(ar(current))
            current = word
    if current:
        lines.append(ar(current))
    return lines


def _draw_letterhead(c, stable_name):
    c.saveState()

    c.setFillColor(PARCHMENT)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    # الشريط العلوي
    c.setFillColor(INK)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)
    if os.path.exists(LOGO_PATH):
        logo_size = 16 * mm
        c.drawImage(
            LOGO_PATH, PAGE_W / 2 - logo_size / 2, PAGE_H - HEADER_H + (HEADER_H - logo_size) / 2,
            width=logo_size, height=logo_size, mask="auto", preserveAspectRatio=True,
        )
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(0, PAGE_H - HEADER_H, PAGE_W, PAGE_H - HEADER_H)

    # الشريط السفلي
    c.setFillColor(INK)
    c.rect(0, 0, PAGE_W, FOOTER_H, fill=1, stroke=0)
    c.setStrokeColor(GOLD)
    c.line(0, FOOTER_H, PAGE_W, FOOTER_H)

    c.setFillColor(GOLD_SOFT)
    c.setFont("Amiri", 8)
    c.drawCentredString(PAGE_W / 2, FOOTER_H / 2 - 2, f"{c.getPageNumber()}")

    c.setFont("Amiri", 9)
    c.drawRightString(PAGE_W - MARGIN, FOOTER_H / 2 - 2, ar(stable_name))

    c.restoreState()


STATUS_LABELS_AR = {
    "done": "منجزة",
    "skipped": "متجاوَزة",
    "pending": "بانتظار التنفيذ",
}


def build_daily_report_pdf(stable, horse, day, tasks):
    """يبني PDF لتقرير المتابعة اليومي لحصان معيّن في يوم محدد، من بيانات DailyTask."""
    _register_fonts()
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    _draw_letterhead(c, stable.name_ar)

    y = CONTENT_TOP

    c.setFillColor(INK)
    c.setFont("Amiri-Bold", 18)
    c.drawCentredString(PAGE_W / 2, y, ar("تقرير المتابعة اليومي"))
    y -= 9 * mm

    c.setFillColor(MUTED)
    c.setFont("Amiri", 10)
    c.drawCentredString(PAGE_W / 2, y, ar(f"{horse.name} — {stable.name_ar} — {day.isoformat()}"))
    y -= 12 * mm

    owner_name = horse.owner.name if horse.owner else "الإسطبل"
    c.setFillColor(INK)
    c.setFont("Amiri", 10.5)
    for line in [f"السلالة: {horse.breed or '—'}", f"اللون: {horse.color or '—'}",
                 f"المالك: {owner_name}"]:
        c.drawRightString(PAGE_W - MARGIN, y, ar(line))
        y -= 6 * mm
    y -= 6 * mm

    # ---------------- جدول المهام (الأعمدة بترتيب RTL: المهمة أقصى اليمين) ----------------
    col_widths = [45 * mm, 30 * mm, 33 * mm, 62 * mm]   # المهمة، الحالة، المسؤول، ملاحظات
    col_labels = ["المهمة", "الحالة", "المسؤول", "ملاحظات"]
    table_w = sum(col_widths)
    table_x = PAGE_W - MARGIN - table_w  # الحافة اليسرى للجدول (يبدأ من اليمين)

    # حدود يمين كل عمود (للرسم RTL): العمود الأول (المهمة) يبدأ من أقصى اليمين
    col_right_edges = []
    cursor = PAGE_W - MARGIN
    for w in col_widths:
        col_right_edges.append(cursor)
        cursor -= w

    def draw_table_header(y_top):
        c.setFillColor(INK)
        c.rect(table_x, y_top - 8 * mm, table_w, 8 * mm, fill=1, stroke=0)
        c.setFont("Amiri-Bold", 9.5)
        c.setFillColor(PARCHMENT)
        for label, right_edge, w in zip(col_labels, col_right_edges, col_widths):
            c.drawRightString(right_edge - 3 * mm, y_top - 5.5 * mm, ar(label))
        return y_top - 8 * mm

    y = draw_table_header(y)

    row_font = "Amiri"
    row_size = 9
    line_h = 4.6 * mm
    cell_pad_top = 2.2 * mm
    cell_pad_lr = 3 * mm

    for t in tasks:
        notes_lines = wrap_ar_lines(t.notes, row_font, row_size, col_widths[3] - 2 * cell_pad_lr)
        row_lines = max(1, len(notes_lines))
        row_h = row_lines * line_h + 2 * cell_pad_top

        if y - row_h < CONTENT_BOTTOM:
            c.showPage()
            _draw_letterhead(c, stable.name_ar)
            y = CONTENT_TOP
            y = draw_table_header(y)

        # حدود الصف
        c.setStrokeColor(MUTED)
        c.setLineWidth(0.4)
        c.rect(table_x, y - row_h, table_w, row_h, fill=0, stroke=1)
        for edge in col_right_edges[:-1]:
            c.line(edge - col_widths[col_right_edges.index(edge)], y - row_h,
                   edge - col_widths[col_right_edges.index(edge)], y)

        c.setFillColor(INK)
        c.setFont(row_font, row_size)
        # المهمة
        c.drawRightString(col_right_edges[0] - cell_pad_lr, y - cell_pad_top - 3, ar(t.type_label))
        # الحالة
        c.drawRightString(col_right_edges[1] - cell_pad_lr, y - cell_pad_top - 3,
                           ar(STATUS_LABELS_AR.get(t.status, t.status)))
        # المسؤول
        c.drawRightString(col_right_edges[2] - cell_pad_lr, y - cell_pad_top - 3,
                           ar(t.assignee_name) if t.assignee_name else "—")
        # ملاحظات (قد تكون عدة أسطر)
        ny = y - cell_pad_top - 3
        for line in notes_lines:
            c.drawRightString(col_right_edges[3] - cell_pad_lr, ny, line)
            ny -= line_h

        y -= row_h

    y -= 8 * mm
    c.setFillColor(MUTED)
    c.setFont("Amiri", 8)
    c.drawRightString(PAGE_W - MARGIN, y, ar(f"تاريخ الإصدار: {datetime.now().strftime('%Y-%m-%d %H:%M')}"))

    c.save()
    buf.seek(0)
    return buf


def build_invoice_pdf(stable, booking):
    """يبني PDF لفاتورة حجز حصة تدريب/ركوب."""
    _register_fonts()
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    _draw_letterhead(c, stable.name_ar)

    y = CONTENT_TOP

    c.setFillColor(INK)
    c.setFont("Amiri-Bold", 20)
    c.drawCentredString(PAGE_W / 2, y, ar("فاتورة حجز"))
    y -= 10 * mm

    c.setFillColor(MUTED)
    c.setFont("Amiri", 10)
    c.drawCentredString(PAGE_W / 2, y, ar(stable.name_ar))
    y -= 16 * mm

    session_date = booking.session_date.isoformat() if booking.session_date else "—"
    payment_label = "مدفوع" if booking.payment_status == "paid" else "غير مدفوع"

    rows_data = [
        ("رقم الفاتورة", f"#{booking.id}"),
        ("اسم الزائر", booking.visitor.name),
        ("الباقة", booking.package.name_ar if booking.package else "—"),
        ("عدد الحصص", str(booking.package.session_count) if booking.package else "—"),
        ("تاريخ الحجز", booking.created_at.strftime("%Y-%m-%d")),
        ("تاريخ الحصة المفضّل", session_date),
        ("حالة الدفع", payment_label),
        ("مرجع الدفع", booking.payment_ref or "—"),
    ]

    table_w = 150 * mm
    table_x = PAGE_W / 2 - table_w / 2
    label_col_w = 55 * mm
    value_col_w = table_w - label_col_w
    row_h = 11 * mm

    c.setLineWidth(0.4)
    for i, (label, value) in enumerate(rows_data):
        row_y_top = y - i * row_h
        c.setStrokeColor(MUTED)
        c.rect(table_x, row_y_top - row_h, table_w, row_h, fill=0, stroke=1)
        c.line(table_x + value_col_w, row_y_top - row_h, table_x + value_col_w, row_y_top)

        c.setFillColor(MUTED)
        c.setFont("Amiri", 10)
        c.drawRightString(table_x + table_w - 4 * mm, row_y_top - row_h / 2 - 3, ar(label))

        c.setFillColor(INK)
        c.setFont("Amiri-Bold", 11)
        c.drawRightString(table_x + value_col_w - 4 * mm, row_y_top - row_h / 2 - 3, ar(str(value)))

    amount_y_top = y - len(rows_data) * row_h
    c.setFillColor(AMOUNT_BG)
    c.rect(table_x, amount_y_top - row_h, table_w, row_h, fill=1, stroke=0)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(table_x, amount_y_top, table_x + table_w, amount_y_top)
    c.setStrokeColor(MUTED)
    c.setLineWidth(0.4)
    c.rect(table_x, amount_y_top - row_h, table_w, row_h, fill=0, stroke=1)
    c.line(table_x + value_col_w, amount_y_top - row_h, table_x + value_col_w, amount_y_top)

    c.setFillColor(MUTED)
    c.setFont("Amiri", 10)
    c.drawRightString(table_x + table_w - 4 * mm, amount_y_top - row_h / 2 - 3, ar("المبلغ الإجمالي"))
    c.setFillColor(INK)
    c.setFont("Amiri-Bold", 13)
    c.drawRightString(table_x + value_col_w - 4 * mm, amount_y_top - row_h / 2 - 3,
                       ar(f"{float(booking.amount):.0f} ر.س"))

    y = amount_y_top - row_h - 14 * mm
    c.setFillColor(MUTED)
    c.setFont("Amiri", 8)
    c.drawCentredString(PAGE_W / 2, y,
                         ar("هذه فاتورة إلكترونية صادرة تلقائيًا من منصّة الخيول السعودية — لا تتطلب توقيعًا أو ختمًا."))

    c.save()
    buf.seek(0)
    return buf
