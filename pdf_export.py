# -*- coding: utf-8 -*-
"""
pdf_export.py —— 错题卡片 PDF 生成模块（reportlab 实现，固定模板，不交给 AI 排版）

页面结构（硬约束，不得调整）：
  第 1 页            ：错题卡片 —— 纯文字题干（+ 可选题目小图），整页宽
  第 2 页及以后      ：解析页 —— 全程 70% / 30% 左右分栏，解析可跨页，跨页后依旧维持分栏
                          左侧 70%：AI 解析（复核后的最终版本）
                          右侧 30%：标题「我的解题过程 / 补充」+ 用户手写图（无图则留白）
  第 3 页            ：同类变式练习 —— 只放 3 道题目，不放解析，整页宽
  第 4 页及以后      ：变式题解析 —— 整页宽，不分栏，页数不限

实现要点：
  1. 用 BaseDocTemplate + PageTemplate + Frame 控制版式；
  2. 解析页的 PageTemplate 只声明「左侧 70%」一个 Frame，
     因此长文本只会在左栏跨页流动，永远不会侵占右侧 30%；
  3. 右侧 30% 的边框、标题、手写图在 onPage 回调里用 canvas 直接绘制（页页都有）；
  4. 全程不写磁盘，PDF 以 bytes 返回，可直接喂给 st.download_button。
"""

from __future__ import annotations

import io
import os
import re
from typing import List, Optional, Tuple

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image as RLImage,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    XPreformatted,
)

# ===========================================================================
# 一、中文字体注册（自动探测常见系统字体，全部失败时回退到 reportlab 内置宋体 CID 字体）
# ===========================================================================

# (常规字体, 粗体字体) 候选路径；按平台顺序尝试
_FONT_CANDIDATES: List[Tuple[str, str]] = [
    # Windows
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
    ("C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/simhei.ttf"),
    ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"),
    # macOS
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
    # Linux / 服务器
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", "/usr/share/fonts/truetype/arphic/uming.ttc"),
]

_REGULAR = "CardCN"      # 注册后的常规字体名
_BOLD = "CardCN-Bold"    # 注册后的粗体字体名
_font_ready = False


def _register_fonts() -> None:
    """探测并注册一套可用的中文字体（幂等，只注册一次）。"""
    global _font_ready
    if _font_ready:
        return

    regular_path = bold_path = None
    for r, b in _FONT_CANDIDATES:
        if os.path.exists(r):
            regular_path = r
            bold_path = b if os.path.exists(b) else r
            break

    if regular_path:
        try:
            pdfmetrics.registerFont(TTFont(_REGULAR, regular_path, subfontIndex=0))
            pdfmetrics.registerFont(TTFont(_BOLD, bold_path, subfontIndex=0))
            pdfmetrics.registerFontFamily(_REGULAR, normal=_REGULAR, bold=_BOLD,
                                          italic=_REGULAR, boldItalic=_BOLD)
            _font_ready = True
            return
        except Exception:
            pass  # 字体文件损坏/格式不支持 → 走内置回退

    # 兜底：reportlab 自带的 Adobe 中文字体（无需字体文件）
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    globals()["_REGULAR"] = "STSong-Light"
    globals()["_BOLD"] = "STSong-Light"
    _font_ready = True


def get_font_names() -> Tuple[str, str]:
    """返回 (常规字体名, 粗体字体名)，供样式构造使用。"""
    _register_fonts()
    return _REGULAR, _BOLD


# ===========================================================================
# 二、页面尺寸与几何常量（分栏比例硬约束：70% / 30%）
# ===========================================================================

PAGE_W, PAGE_H = A4
MARGIN_X = 2.0 * cm          # 左右页边距
MARGIN_TOP = 2.0 * cm        # 上页边距
MARGIN_BOTTOM = 1.8 * cm     # 下页边距

CONTENT_W = PAGE_W - 2 * MARGIN_X          # 正文可用宽度
FRAME_H = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM

LEFT_RATIO = 0.70            # 左栏 70%（硬约束）
RIGHT_RATIO = 0.30           # 右栏 30%（硬约束）
GUTTER = 4                   # 分隔线与左栏文字之间的视觉间隙（pt，从右栏内部扣除）

# 左栏宽度严格等于可用宽度的 70%；分隔线画在 70% 分界处，间隙从右栏里扣，
# 保证「左 70% / 右 30%」的比例精确成立。
LEFT_W = CONTENT_W * LEFT_RATIO
RIGHT_X = MARGIN_X + CONTENT_W * LEFT_RATIO
RIGHT_W = CONTENT_W * RIGHT_RATIO

FRAME_Y = MARGIN_BOTTOM      # Frame 的 y 起点（左下角坐标系）


# ===========================================================================
# 三、Markdown → reportlab flowables（极简渲染器）
# ===========================================================================

# 常见 LaTeX / 数学符号的可读化替换表（PDF 不渲染公式，尽量转成 Unicode 字符）
_LATEX_MAP = [
    (r"\times", "×"), (r"\div", "÷"), (r"\cdot", "·"), (r"\pm", "±"),
    (r"\leq", "≤"), (r"\le", "≤"), (r"\geq", "≥"), (r"\ge", "≥"),
    (r"\neq", "≠"), (r"\approx", "≈"), (r"\infty", "∞"),
    (r"\alpha", "α"), (r"\beta", "β"), (r"\gamma", "γ"), (r"\theta", "θ"),
    (r"\pi", "π"), (r"\Delta", "Δ"), (r"\angle", "∠"), (r"\triangle", "△"),
    (r"\parallel", "∥"), (r"\perp", "⊥"), (r"\sqrt", "√"),
    (r"\rightarrow", "→"), (r"\Rightarrow", "⇒"), (r"\because", "∵"),
    (r"\therefore", "∴"), (r"\in", "∈"), (r"\sum", "Σ"),
    (r"\left", ""), (r"\right", ""), (r"\dfrac", ""), (r"\tfrac", ""),
    (r"\;", " "), (r"\,", " "), (r"\!", ""), (r"{", ""), (r"}", ""),
]

# 上标/下标数字
_SUP_MAP = str.maketrans({"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
                          "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
                          "n": "ⁿ", "+": "⁺", "-": "⁻"})


def _latex_readable(text: str) -> str:
    """把 LaTeX 片段尽量转成 Unicode 可读文本（PDF 无法渲染公式时的折中方案）。"""
    s = text
    for src, dst in _LATEX_MAP:
        s = s.replace(src, dst)
    # x^2 → x²，x^{2n} → x²ⁿ
    s = re.sub(r"\^\{?([0-9n+\-]{1,3})\}?", lambda m: m.group(1).translate(_SUP_MAP), s)
    # \frac{a}{b} → a/b（只处理单层，嵌套公式保持原文，避免过度破坏）
    s = re.sub(r"\\frac\s*([^\s{}]+)\s*([^\s{}]+)", r"\1/\2", s)
    # 去掉残余的反斜杠命令（如 \mathrm、\text 之类）
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    return s.strip()


def _escape_xml(text: str) -> str:
    """XML 转义：reportlab 的 Paragraph 使用类 XML 标记，必须转义 & < >"""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def inline_md(text: str) -> str:
    """
    把一行的 Markdown 行内标记转换为 reportlab Paragraph 支持的小 markup。
    支持：**粗体** *斜体* `代码` $公式$ ~~删除线~~
    """
    s = text
    # 先抽出公式，避免公式里的 * _ 被当成强调标记
    formulas: List[str] = []

    def _stash(m):
        formulas.append(_latex_readable(m.group(1)))
        return f"\x00F{len(formulas) - 1}\x00"

    s = re.sub(r"\$\$(.+?)\$\$", _stash, s, flags=re.DOTALL)
    s = re.sub(r"\\?\$(.+?)\$", _stash, s, flags=re.DOTALL)
    s = re.sub(r"\\\((.+?)\\\)", _stash, s, flags=re.DOTALL)
    s = re.sub(r"\\\[(.+?)\\\]", _stash, s, flags=re.DOTALL)

    s = _escape_xml(s)

    # 行内代码
    s = re.sub(r"`([^`]+)`", r'<font face="%s" size="10">\1</font>' % _REGULAR, s)
    # 粗体 / 斜体 / 删除线
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)

    # 还原公式（用等宽 + 斜体突出，保证可读性）
    for i, f in enumerate(formulas):
        s = s.replace(f"\x00F{i}\x00", f'<font face="{_REGULAR}" size="10.5">{_escape_xml(f)}</font>')

    return s


def _make_styles() -> dict:
    """构造 PDF 用的段落样式集。"""
    reg, bold = get_font_names()
    base = dict(fontName=reg, wordWrap="CJK")   # wordWrap=CJK 才能让中文正常换行

    return {
        "title": ParagraphStyle("title", parent=None, fontName=bold, fontSize=17,
                                leading=24, alignment=TA_LEFT, spaceAfter=10,
                                textColor=colors.HexColor("#111111"), wordWrap="CJK"),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=14, leading=20,
                             spaceBefore=8, spaceAfter=6,
                             textColor=colors.HexColor("#1a1a1a"), wordWrap="CJK"),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12.5, leading=18,
                             spaceBefore=6, spaceAfter=4,
                             textColor=colors.HexColor("#222222"), wordWrap="CJK"),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=11.5, leading=17,
                             spaceBefore=4, spaceAfter=3,
                             textColor=colors.HexColor("#333333"), wordWrap="CJK"),
        "body": ParagraphStyle("body", fontName=reg, fontSize=11, leading=19,
                               spaceAfter=4, alignment=TA_LEFT,
                               textColor=colors.HexColor("#1a1a1a"), wordWrap="CJK"),
        "question": ParagraphStyle("question", fontName=reg, fontSize=12.5,
                                   leading=22, spaceAfter=6, alignment=TA_LEFT,
                                   textColor=colors.HexColor("#000000"), wordWrap="CJK"),
        "list": ParagraphStyle("list", fontName=reg, fontSize=11, leading=19,
                               leftIndent=16, bulletIndent=2, spaceAfter=3,
                               wordWrap="CJK"),
        "code": ParagraphStyle("code", fontName=reg, fontSize=10, leading=15,
                               leftIndent=10, spaceBefore=4, spaceAfter=6,
                               textColor=colors.HexColor("#333333"), wordWrap="CJK"),
        "note": ParagraphStyle("note", fontName=reg, fontSize=9, leading=13,
                               textColor=colors.HexColor("#888888"), wordWrap="CJK"),
        "center": ParagraphStyle("center", fontName=reg, fontSize=10.5, leading=16,
                                 alignment=TA_CENTER, wordWrap="CJK"),
    }


_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_HR_RE = re.compile(r"^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$")
_BULLET_RE = re.compile(r"^\s*([-*+])\s+(.*)$")
_ORDER_RE = re.compile(r"^\s*(\d+)\s*[\.、．)）]\s*(.*)$")

# 不含空白、且不含中日韩字符的「超长连续串」（如长英文单词、长链接、base64）
_LONG_TOKEN_RE = re.compile(r"[^\s\u2e80-\u9fff\uff00-\uffef]{30,}")


def _break_long_tokens(line: str, size: int = 25) -> str:
    """
    把超长连续串按固定长度插入空格。
    原因：reportlab 无法对没有空格的长串换行，会导致文字冲出栏宽、压到右侧 30% 区域。
    中文可正常换行，因此只在非 CJK 串上生效，正常英文单词（长度远小于 30）不受影响。
    """
    def _rep(m):
        token = m.group(0)
        return " ".join(token[i:i + size] for i in range(0, len(token), size))
    return _LONG_TOKEN_RE.sub(_rep, line)


def md_to_flowables(md_text: str, styles: dict, body_style: str = "body") -> list:
    """
    极简 Markdown 渲染器：把 Markdown 文本转成 reportlab 的 flowable 列表。
    支持：标题、无序/有序列表、分隔线、代码块、表格（降级为文本）、行内标记。
    """
    if not md_text or not md_text.strip():
        return []

    lines = md_text.replace("\r\n", "\n").split("\n")
    flows: list = []
    buf: List[str] = []      # 普通段落缓冲区（连续行合并为一段）

    def flush_buf():
        """把缓冲区里的连续行合并成一个段落。"""
        if not buf:
            return
        text = ""
        for i, seg in enumerate(buf):
            if i == 0:
                text = seg
            elif text and text[-1].isascii() and seg[:1].isascii() and text[-1].isalnum():
                text += " " + seg      # 英文/数字之间补空格
            else:
                text += seg            # 中文直接拼接
        if text.strip():
            flows.append(Paragraph(inline_md(text), styles[body_style]))
        buf.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 1) 空行 → 结束当前段落
        if not stripped:
            flush_buf()
            i += 1
            continue

        # 2) 代码块 ```
        if stripped.startswith("```"):
            flush_buf()
            code_lines: List[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 跳过收尾的 ```
            if code_lines:
                flows.append(XPreformatted(_escape_xml("\n".join(code_lines)), styles["code"]))
            continue

        # 3) 分隔线
        if _HR_RE.match(line):
            flush_buf()
            flows.append(Spacer(1, 4))
            flows.append(HRFlowable(width="100%", thickness=0.6,
                                    color=colors.HexColor("#cccccc"),
                                    spaceBefore=2, spaceAfter=6))
            continue

        # 4) 标题
        m = _HEADING_RE.match(line)
        if m:
            flush_buf()
            level = len(m.group(1))
            style_key = "h1" if level <= 2 else ("h2" if level == 3 else "h3")
            flows.append(Paragraph(inline_md(_break_long_tokens(m.group(2))), styles[style_key]))
            i += 1
            continue

        # 5) 列表项
        m = _ORDER_RE.match(line)
        if m:
            flush_buf()
            flows.append(Paragraph(inline_md(_break_long_tokens(m.group(2))), styles["list"],
                                   bulletText=f"{m.group(1)}."))
            i += 1
            continue
        m = _BULLET_RE.match(line)
        if m:
            flush_buf()
            flows.append(Paragraph(inline_md(_break_long_tokens(m.group(2))), styles["list"],
                                   bulletText="•"))
            i += 1
            continue

        # 6) Markdown 表格 → 降级：去掉竖线与分隔行，按普通文本排
        if stripped.startswith("|"):
            flush_buf()
            if re.match(r"^\|?[\s:\-|]+\|?$", stripped):
                i += 1
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            buf.append("    ".join(_break_long_tokens(c) for c in cells))
            i += 1
            continue

        # 7) 普通文本行
        buf.append(_break_long_tokens(stripped))
        i += 1

    flush_buf()
    return flows


# ===========================================================================
# 四、图片缩放辅助
# ===========================================================================

def _pil_fit(img: Image.Image, max_w: float, max_h: float):
    """
    把 PIL 图片等比缩放到 (max_w, max_h) 之内，返回 (缩放后的 PIL 图, 宽, 高)。
    统一转 RGB，避免 CMYK / 带透明通道的图在 PDF 中报错；只缩小不放大。
    """
    im = img.convert("RGB")
    w, h = im.size
    if w <= 0 or h <= 0:
        return None, 0, 0
    scale = min(max_w / float(w), max_h / float(h), 1.0)
    new_w, new_h = w * scale, h * scale
    im = im.resize((max(1, int(new_w)), max(1, int(new_h))), Image.LANCZOS)
    return im, new_w, new_h


def _pil_to_png_bytes(im: Image.Image) -> io.BytesIO:
    """PIL 图 → 内存 PNG 字节流（reportlab 的 Image 流只接受文件对象，不接受 PIL 对象）。"""
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


# ===========================================================================
# 五、主函数：生成错题卡片 PDF
# ===========================================================================

def build_card_pdf(
    question_text: str,
    question_image: Optional[Image.Image],
    analysis_text: str,
    handwriting_image: Optional[Image.Image],
    variants_text: str,
    variant_solutions_text: str,
) -> bytes:
    """
    按固定模板生成错题卡片 PDF，返回 PDF 二进制内容（不落盘）。

    :param question_text:           第 1 页的纯文字题干（用户在网页上确认/修改后的版本）
    :param question_image:          第 1 页可选插入的题目小图（None 表示不插）
    :param analysis_text:           第 2 页左侧 70% 的解析（复核后的最终版本）
    :param handwriting_image:       第 2 页右侧 30% 的手写解题图（None 表示留白）
    :param variants_text:           第 3 页的 3 道变式题（只放题目）
    :param variant_solutions_text:  第 4 页及以后的变式题解析
    :return: PDF 文件的 bytes
    """
    get_font_names()
    styles = _make_styles()

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN_X, rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM,
        title="错题卡片", author="错题卡片生成器",
    )

    # ---- Frame 定义 -------------------------------------------------------
    full_frame = Frame(MARGIN_X, FRAME_Y, CONTENT_W, FRAME_H, id="full",
                       leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    # 解析页：只声明左栏 70% 一个 Frame → 内容跨页也只会留在左栏。
    # rightPadding 让正文与分隔线之间留出 4pt 安全距离（避免中文标点悬挂压线）。
    left_frame = Frame(MARGIN_X, FRAME_Y, LEFT_W, FRAME_H, id="left",
                       leftPadding=0, rightPadding=GUTTER,
                       topPadding=0, bottomPadding=0)

    # ---- 右侧 30% 区域的绘制（每一页都画，保证区域始终存在） --------------
    right_title = "我的解题过程 / 补充"
    hw_img = None
    hw_w = hw_h = 0.0
    if handwriting_image is not None:
        # 图片可用区域：右栏宽度留 12pt 内边距，高度最多占右栏可用高度的 72%
        hw_img, hw_w, hw_h = _pil_fit(
            handwriting_image, RIGHT_W - 12, FRAME_H * 0.72
        )

    state = {"first_split_page": None}

    def draw_right_column(canvas, doc_):
        """在右侧 30% 画分隔线、标题，并在解析首页贴上手写图（无图则纯留白）。"""
        canvas.saveState()
        # 竖向分隔虚线（画在 70% 分界处）
        sep_x = RIGHT_X - GUTTER / 2
        canvas.setStrokeColor(colors.HexColor("#cccccc"))
        canvas.setLineWidth(0.8)
        canvas.setDash(3, 3)
        canvas.line(sep_x, MARGIN_BOTTOM, sep_x, PAGE_H - MARGIN_TOP + 6)
        canvas.setDash()

        # 右栏标题（从分隔线往右 6pt 开始，右侧留出 6pt 内边距）
        reg, bold = get_font_names()
        text_x = RIGHT_X + 6
        available_w = RIGHT_W - 12
        canvas.setFont(bold, 10.5)
        canvas.setFillColor(colors.HexColor("#333333"))
        title_y = PAGE_H - MARGIN_TOP - 2
        canvas.drawString(text_x, title_y, right_title)
        canvas.setStrokeColor(colors.HexColor("#dddddd"))
        canvas.setLineWidth(0.6)
        canvas.line(text_x, title_y - 4, text_x + available_w, title_y - 4)

        # 手写图：只画在解析区的第一页，避免跨页重复贴图
        pn = canvas.getPageNumber()
        if state["first_split_page"] is None:
            state["first_split_page"] = pn
        if hw_img is not None and pn == state["first_split_page"]:
            from reportlab.lib.utils import ImageReader
            img_x = text_x + (available_w - hw_w) / 2.0
            img_y = title_y - 16 - hw_h
            canvas.drawImage(ImageReader(hw_img), img_x, img_y,
                             width=hw_w, height=hw_h, mask="auto")

        canvas.restoreState()

    def draw_footer(canvas, doc_):
        """页脚：居中小页码。"""
        canvas.saveState()
        canvas.setFont(_REGULAR, 8)
        canvas.setFillColor(colors.HexColor("#999999"))
        canvas.drawCentredString(PAGE_W / 2.0, MARGIN_BOTTOM * 0.45,
                                 f"— {canvas.getPageNumber()} —")
        canvas.restoreState()

    def on_front(canvas, doc_):
        draw_footer(canvas, doc_)

    def on_split(canvas, doc_):
        draw_right_column(canvas, doc_)
        draw_footer(canvas, doc_)

    def on_plain(canvas, doc_):
        draw_footer(canvas, doc_)

    # ---- 注册页面模板 -----------------------------------------------------
    doc.addPageTemplates([
        PageTemplate(id="front", frames=[full_frame], onPage=on_front),
        PageTemplate(id="split", frames=[left_frame], onPage=on_split),
        PageTemplate(id="plain", frames=[full_frame], onPage=on_plain),
    ])

    # ---- 组装内容 ---------------------------------------------------------
    story: list = []

    # ===== 第 1 页：错题卡片（题干 + 可选小图）=====
    story.append(Paragraph("错题卡片", styles["title"]))
    q_text = (question_text or "").strip() or "（题干为空）"
    for line in q_text.split("\n"):
        if line.strip():
            story.append(Paragraph(inline_md(line.strip()), styles["question"]))
        else:
            story.append(Spacer(1, 6))

    if question_image is not None:
        q_img, w, h = _pil_fit(question_image, CONTENT_W * 0.62, 7.0 * cm)
        if q_img is not None:
            story.append(Spacer(1, 10))
            story.append(RLImage(_pil_to_png_bytes(q_img), width=w, height=h, hAlign="LEFT"))

    # ===== 第 2 页及以后：解析页（70 / 30 分栏，可跨页）=====
    story.append(NextPageTemplate("split"))
    story.append(PageBreak())
    story.append(Paragraph("AI 解析（已复核）", styles["h1"]))
    story.append(Spacer(1, 4))
    a_flows = md_to_flowables(analysis_text, styles, body_style="body")
    story.extend(a_flows if a_flows else
                 [Paragraph("（暂无解析内容）", styles["body"])])

    # ===== 第 3 页：同类变式练习（只放题目，整页宽）=====
    story.append(NextPageTemplate("plain"))
    story.append(PageBreak())
    story.append(Paragraph("同类变式练习", styles["title"]))
    story.append(Spacer(1, 6))
    v_flows = md_to_flowables(variants_text, styles, body_style="body")
    story.extend(v_flows if v_flows else
                 [Paragraph("（暂无变式题）", styles["body"])])

    # ===== 第 4 页及以后：变式题解析（整页宽，不限页数）=====
    story.append(PageBreak())
    story.append(Paragraph("变式题解析", styles["title"]))
    story.append(Spacer(1, 6))
    s_flows = md_to_flowables(variant_solutions_text, styles, body_style="body")
    story.extend(s_flows if s_flows else
                 [Paragraph("（暂无变式题解析）", styles["body"])])

    doc.build(story)
    buf.seek(0)
    return buf.read()
