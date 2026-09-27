# -*- coding: utf-8 -*-
"""
md_utils.py —— Markdown 文本工具模块（纯 Python 实现，不依赖第三方库）

本模块负责三件事：
1. 将 GLM 返回的 Markdown 长文本，按「一级/二级标题」切成若干段落块；
2. 从切好的段落块中，智能识别出「AI 解析 / 变式练习题 / 变式题解析」三部分内容；
3. 提供 Markdown 标记清理函数，方便在网页或 PDF 中做纯文本回退展示。

之所以不直接用第三方 Markdown 库：PDF 排版由 reportlab 负责（reportlab 不支持 Markdown），
网页端由 Streamlit 原生 st.markdown 渲染，本模块只做「拆解 + 兜底」，保证任何格式的输出都能被正确分段。
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# 一、按标题切分 Markdown
# ---------------------------------------------------------------------------

# 匹配 Markdown 标题行，例如： "# 解析"  "## 二、变式练习"  "### 1. 第一题"
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")


def split_by_headings(md_text: str) -> List[Tuple[str, str]]:
    """
    把 Markdown 文本按标题切成 [(标题, 正文), ...]。

    :param md_text: 原始 Markdown 文本
    :return: 列表，每项为 (标题文本, 该标题下的正文)。文本开头没有标题时，标题记为 ""
    """
    if not md_text:
        return []

    lines = md_text.replace("\r\n", "\n").split("\n")
    blocks: List[Tuple[str, str]] = []
    cur_title = ""
    cur_body: List[str] = []

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:  # 遇到新标题，先把上一块收尾
            blocks.append((cur_title, "\n".join(cur_body).strip()))
            cur_title = m.group(2).strip()
            cur_body = []
        else:
            cur_body.append(line)

    # 收尾最后一块
    blocks.append((cur_title, "\n".join(cur_body).strip()))
    return blocks


# ---------------------------------------------------------------------------
# 二、从模型输出中提取「解析 / 变式题 / 变式解析」
# ---------------------------------------------------------------------------

# 用于判断某个标题属于哪一类的关键词（命中即归类）
_KEY_ANALYSIS = ("解析", "解答", "详解", "解题思路", "答案")
_KEY_VARIANTS = ("变式练习", "变式题", "同类练习", "相似题", "同类题")
_KEY_VAR_SOL = ("变式解析", "变式答案", "练习解析", "变式题解析", "参考答案")


def _match_keywords(title: str, keywords: Tuple[str, ...]) -> bool:
    """判断标题是否命中关键词组（不区分大小写）。"""
    t = (title or "").lower()
    return any(k.lower() in t for k in keywords)


def parse_solution_bundle(md_text: str) -> Dict[str, str]:
    """
    解析 GLM-4-Flash 第一轮生成的结果，拆成三个字段。

    :param md_text: 模型原始 Markdown 输出
    :return: {"analysis": 原始AI解析,
              "variants": 3道变式题（只含题目）,
              "variant_solutions": 3道变式题解析}
    说明：本函数做了多层兜底。即便模型没有按约定标题输出，也会尽量给出合理分段，
          最差情况下把全文当作解析，保证程序不崩。
    """
    result = {"analysis": "", "variants": "", "variant_solutions": ""}
    if not md_text or not md_text.strip():
        return result

    blocks = split_by_headings(md_text)

    # 第一遍：严格按标题关键词归类
    for title, body in blocks:
        if not body:
            continue
        if _match_keywords(title, _KEY_VAR_SOL):
            result["variant_solutions"] += (body + "\n\n")
        elif _match_keywords(title, _KEY_VARIANTS):
            result["variants"] += (body + "\n\n")
        elif _match_keywords(title, _KEY_ANALYSIS):
            result["analysis"] += (body + "\n\n")

    # 第二遍兜底：若「变式解析」被误判进了「变式题」（因为两个标题都含"变式"），
    # 这里用「是否包含 '解析' 且位于文本后半段」来二次校正。
    if result["variants"] and not result["variant_solutions"]:
        v_blocks = [b for b in split_by_headings(result["variants"]) if b[1]]
        if len(v_blocks) >= 2:
            # 分成题目块与解析块（通常各一半）
            mid = len(v_blocks) // 2
            result["variants"] = "\n\n".join(b[1] for b in v_blocks[:mid])
            result["variant_solutions"] = "\n\n".join(b[1] for b in v_blocks[mid:])

    # 第三遍兜底：三段都为空 → 整篇当作解析
    if not result["analysis"] and not result["variants"]:
        result["analysis"] = md_text.strip()

    return {k: v.strip() for k, v in result.items()}


def parse_review_result(md_text: str) -> Tuple[bool, str]:
    """
    解析复核环节的模型输出。

    :param md_text: 复核模型返回的文本
    :return: (是否校验通过, 提取到的「修正后解析」文本；通过时第二个返回值为空串)

    判定规则：
      - 出现「解析校验通过」→ 判定通过；
      - 出现「发现错误 / 有误 / 修正」等 → 判定不通过，并把正文作为修正版解析返回；
      - 都不出现 → 保守判定为「不通过」，提醒用户人工核对。
    """
    text = (md_text or "").strip()
    if not text:
        return False, ""

    passed = "解析校验通过" in text

    # 提取「修正后解析」：
    # 1) 优先匹配方括号/中文括号的小节标记，如 "[修正后解析]" "【修正后解析】："
    fixed = ""
    m = re.search(
        r"[\[【]\s*(?:修正后解析|修改后的解析|修正解析|正确解析|最终解析)\s*[\]】]\s*[:：]?\s*(.+)",
        text, flags=re.DOTALL)
    if m:
        fixed = m.group(1).strip()

    # 2) 其次匹配 Markdown 标题，如 "## 修正后解析"
    if not fixed:
        for title, body in split_by_headings(text):
            if any(k in title for k in ("修正", "正确解析", "最终解析", "修改后的解析")):
                fixed = body
                break

    # 3) 仍没找到：整体去掉结论行/错误位置行后作为修正版
    if not fixed and not passed:
        cleaned = re.sub(r"^\s*[\[【]?\s*(?:校验结论|错误位置)\s*[\]】]?\s*[:：]?.*$",
                         "", text, flags=re.MULTILINE)
        fixed = cleaned.strip()

    return passed, ("" if passed else fixed)


def extract_error_position(review_text: str) -> str:
    """从复核输出中抽取「错误位置」小节，便于在页面上单独高亮。"""
    if not review_text:
        return ""
    m = re.search(r"[\[【]\s*(?:错误位置|问题位置)\s*[\]】]\s*[:：]?\s*(.+?)(?=[\[【]|$)",
                  review_text, flags=re.DOTALL)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# 三、Markdown 标记清理（纯文本回退用）
# ---------------------------------------------------------------------------

def strip_markdown(md_text: str) -> str:
    """
    把 Markdown 转成朴素纯文本：去掉 #、*、`、> 等标记，保留文字与基本缩进。
    用于模型输出极端异常时的兜底显示。
    """
    if not md_text:
        return ""
    text = md_text.replace("\r\n", "\n")
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)  # 标题符号
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                        # 加粗
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)   # 斜体
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)                   # 行内代码
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)             # 引用
    text = re.sub(r"^\s*[-*+]\s+", "· ", text, flags=re.MULTILINE)       # 无序列表
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.MULTILINE)       # 表格行（简易丢弃）
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def count_variant_items(variants_text: str) -> int:
    """
    统计变式题数量：按行首的 "1." "2)" "（1）" 等编号计数，用于页面上的数量提示。
    """
    if not variants_text:
        return 0
    pattern = re.compile(r"^\s*(?:\d+\s*[.、．)）]|[(（]\s*\d+\s*[)）])\s*\S")
    return sum(1 for line in variants_text.split("\n") if pattern.match(line))
