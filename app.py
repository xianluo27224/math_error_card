# -*- coding: utf-8 -*-
""" app.py —— 错题卡片生成器（Streamlit 主程序） 整体流程： ① 打开网页 → 先看到「API Key 验证页」，验证通过才能进入工具 ② 上传试卷图片 → 旋转 / 裁切出本题区域 ③ 调用 glm-4.1v-thinking-flash 识别印刷体原题（忽略手写） ④ 题干可在文本框里手动修正 ⑤ 调用 glm-4-flash 生成「AI 解析 + 3 道变式题 + 变式解析」 ⑥ 自动复核：把题干 + 解析再提交一次，验算计算/逻辑/公式 ⑦ 上传手写解题图 → 预览卡片 / 导出固定模板 PDF 安全说明： · API Key 仅保存在 st.session_state（浏览器会话内存），不写文件、不入库、不外传； · 刷新或关闭页面即丢失，需要重新输入； · 上传的图片只存在于内存，程序退出即清理，不做任何持久化。 """

from __future__ import annotations

import io
import sys
import os
import base64
from typing import Optional

import streamlit as st
from PIL import Image

# 保证在任意工作目录下都能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from md_utils import (  # noqa: E402
    parse_solution_bundle,
    parse_review_result,
    extract_error_position,
    strip_markdown,
)
from pdf_export import build_card_pdf, get_font_names                             # noqa: E402
from zhipu_client import ZhipuClient                                              # noqa: E402

# ===========================================================================
# 全局配置
# ===========================================================================

st.set_page_config(
    page_title="错题卡片生成器",
    page_icon="📇",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# 极简样式：只做基础留白与字号控制，不做花哨装饰
st.markdown(
    """ <style> .block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 860px; } .small-tip { color: #8a8a8a; font-size: 13px; line-height: 1.6; } .section-title { font-size: 16px; font-weight: 600; margin: 18px 0 8px; } hr { margin: 1.2rem 0; } </style> """,
    unsafe_allow_html=True,
)

# 必须展示在页面底部的提示（需求指定文案）
FOOTER_TIP = (
    "> **重要提醒**：AI识别、AI解题存在幻觉，生成的题干、答案、变式题，请务必人工核对。"
    "免费API存在限流，短时间大量提交会临时报错。"
)


# ===========================================================================
# 会话状态初始化
# ===========================================================================

def init_state() -> None:
    """初始化所有会话变量。注意：API Key 只存在于这里，绝不落盘。"""
    defaults = {
        "authed": False,          # 密钥是否已验证通过
        "api_key": "",            # 内存中的密钥（刷新即失效）
        "client": None,           # ZhipuClient 实例
        "question_text": "",      # VL 识别出的题干（可手动修改）
        "analysis": "",           # 原始 AI 解析
        "review_text": "",        # 复核模型原始输出
        "review_passed": None,    # 复核是否通过（True/False/None）
        "fixed_analysis": "",     # 复核给出的修正版解析
        "final_choice": "auto",   # PDF 中最终采用哪一版解析
        "variants": "",           # 3 道变式题（只含题目）
        "variant_solutions": "",  # 3 道变式题解析
        "show_preview": False,    # 是否展示卡片预览
        # 试卷图 / 手写图的旋转角度
        "q_rot": 0,
        "h_rot": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


# ===========================================================================
# 一、API Key 验证页
# ===========================================================================

def page_auth() -> None:
    """密钥验证页：未通过验证前，主功能一律不可见。"""
    st.title("错题卡片生成器")
    st.caption("开始之前，请先验证你的智谱开放平台 API Key")

    st.markdown("---")

    api_key = st.text_input(
        "智谱开放平台 API Key",
        value=st.session_state.api_key,
        type="password",
        placeholder="粘贴你的 API Key，形如 xxxxx.yyyyyyyyy",
        help="密钥只保存在当前浏览器会话内存中，不会写入文件、数据库，也不会上传到任何第三方服务器。",
    )

    col_test, col_enter = st.columns([1, 1])

    with col_test:
        if st.button("测试连接", type="primary", use_container_width=True):
            if not api_key.strip():
                st.error("请先输入 API Key 再测试连接。")
            else:
                with st.spinner("正在验证密钥，请稍候…"):
                    try:
                        client = ZhipuClient(api_key.strip())
                        ok, msg = client.test_connection()
                    except Exception as exc:  # noqa: BLE001
                        ok, msg = False, f"无法完成验证：{exc}"

                if ok:
                    # 只在内存里保存，不写任何文件
                    st.session_state.api_key = api_key.strip()
                    st.session_state.client = client
                    st.session_state.auth_ok = True
                    st.success("✅ 密钥验证通过")
                else:
                    st.session_state.auth_ok = False
                    st.error(f"❌ {msg}")

    with col_enter:
        # 仅当本次会话已验证通过时才允许进入
        if st.session_state.get("auth_ok", False):
            if st.button("进入工具 →", type="primary", use_container_width=True):
                st.session_state.authed = True
                st.rerun()
        else:
            st.button("进入工具 →", use_container_width=True, disabled=True)

    st.markdown("---")
    st.markdown(
        '<div class="small-tip">'
        "· 没有 API Key？前往 <b>智谱开放平台 open.bigmodel.cn</b> 注册并在「API Keys」页面创建。<br>"
        "· 本项目使用免费模型 glm-4-flash 与 glm-4.1v-thinking-flash。<br>"
        "· 密钥仅保存在当前浏览器会话内存中，刷新页面或关闭网页后失效，需要重新输入。"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(FOOTER_TIP)


# ===========================================================================
# 二、图片处理工具（旋转 + 裁切，纯 PIL，不依赖额外组件）
# ===========================================================================

def _preprocess_for_vl(img: Image.Image, max_side: int = 1600) -> str:
    """ 压缩图片并转 base64，避免手机拍照过大导致 API 失败。 返回 JPEG 格式的 base64 字符串。 """
    im = img.convert("RGB")
    w, h = im.size
    longest = max(w, h)
    if longest > max_side:
        ratio = max_side / longest
        im = im.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _recognize_question_vl(img: Image.Image, api_key: str) -> str:
    """ 直接调用智谱多模态（glm-4v-flash）识别印刷体题干，忽略手写。 不依赖 zhipu_client.py 里的实现，确保模型和提示词正确。 """
    from zhipuai import ZhipuAI
    client = ZhipuAI(api_key=api_key)
    img_b64 = _preprocess_for_vl(img)
    resp = client.chat.completions.create(
        model="glm-4v-flash",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "你是OCR识别助手，只提取图片里面印刷的数学原题题干。\n"
                        "规则：\n"
                        "1. 忽略所有手写字迹、草稿、手写答案、划线标记；\n"
                        "2. 保留原题的数字、公式、几何文字条件；\n"
                        "3. 不要解释题目，不要作答，只输出提取出来的纯题干文本；\n"
                        "4. 如果图片没有印刷题目，直接返回空字符串。"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                },
            ],
        }],
    )
    return resp.choices[0].message.content.strip()


def rotate_and_crop(img: Image.Image, rot: int, top: int, bottom: int, left: int, right: int) -> Image.Image:
    """ 对图片做旋转 + 四边百分比裁切。 :param rot: 旋转角度（逆时针为正），PIL 的 rotate 默认逆时针 :param top/bottom/left/right: 四边裁掉的百分比（0~49） """
    im = img.convert("RGB")
    if rot:
        im = im.rotate(rot, expand=True)          # expand=True 避免旋转后被裁掉边角
    w, h = im.size
    x0 = int(w * left / 100.0)
    x1 = int(w * (100 - right) / 100.0)
    y0 = int(h * top / 100.0)
    y1 = int(h * (100 - bottom) / 100.0)
    # 防御：保证裁切框合法，且至少留下 1 像素
    x0, x1 = max(0, min(x0, w - 1)), max(1, min(x1, w))
    y0, y1 = max(0, min(y0, h - 1)), max(1, min(y1, h))
    if x1 <= x0:
        x1 = x0 + 1
    if y1 <= y0:
        y1 = y0 + 1
    return im.crop((x0, y0, x1, y1))


def image_editor(img: Image.Image, prefix: str, label: str) -> Image.Image:
    """ 渲染一个「旋转 + 裁切」编辑区，返回处理后的图片。 用滑块实现裁切，避免额外安装 streamlit 裁切组件，兼容性最好。 """
    st.caption(label)

    # --- 旋转 ---
    c1, c2, c3 = st.columns([1, 1, 2])
    rot_key = f"{prefix}_rot"
    if c1.button("↺ 逆时针 90°", key=f"{prefix}_btn_l", use_container_width=True):
        st.session_state[rot_key] = (st.session_state.get(rot_key, 0) + 90) % 360
        st.rerun()
    if c2.button("↻ 顺时针 90°", key=f"{prefix}_btn_r", use_container_width=True):
        st.session_state[rot_key] = (st.session_state.get(rot_key, 0) - 90) % 360
        st.rerun()
    c3.markdown(f'<div class="small-tip">当前旋转：{st.session_state.get(rot_key, 0)}°</div>',
                unsafe_allow_html=True)

    # --- 裁切（四边各裁掉百分之多少）---
    with st.expander("手动框选裁切（拖动滑块切掉多余部分）", expanded=False):
        a, b = st.columns(2)
        top = a.slider("上边裁掉 %", 0, 49, 0, key=f"{prefix}_top")
        bottom = b.slider("下边裁掉 %", 0, 49, 0, key=f"{prefix}_bottom")
        c, d = st.columns(2)
        left = c.slider("左边裁掉 %", 0, 49, 0, key=f"{prefix}_left")
        right = d.slider("右边裁掉 %", 0, 49, 0, key=f"{prefix}_right")
        st.markdown('<div class="small-tip">只需保留「这一道题」的区域，识别会更准确。</div>',
                    unsafe_allow_html=True)

    return rotate_and_crop(img, st.session_state.get(rot_key, 0),
                           top, bottom, left, right)


# ===========================================================================
# 三、文本展示工具（Markdown 渲染，异常可降级为纯文本）
# ===========================================================================

def show_md(title: str, text: str, key: str, default_md: bool = True) -> None:
    """ 展示一段 AI 生成的 Markdown 文本。 提供「纯文本模式」开关，防止个别 LaTeX 语法导致渲染异常。 """
    if not text:
        st.info("暂无内容")
        return
    plain = st.toggle("纯文本模式（Markdown 显示异常时打开）",
                      value=not default_md, key=f"toggle_{key}")
    if plain:
        st.text(strip_markdown(text))
    else:
        st.markdown(text)


# ===========================================================================
# 四、主功能页
# ===========================================================================

def page_main() -> None:
    client: Optional[ZhipuClient] = st.session_state.client

    # 顶部：标题 + 退出（清除密钥）
    head_l, head_r = st.columns([3, 1])
    with head_l:
        st.title("错题卡片生成器")
    with head_r:
        if st.button("退出并清除密钥", use_container_width=True):
            for k in ("authed", "api_key", "client", "auth_ok"):
                st.session_state.pop(k, None)
            st.rerun()

    st.markdown("---")

    # ------------------------------------------------------------------
    # 第一块：上传试卷图片 + 裁切
    # ------------------------------------------------------------------
    st.markdown('<div class="section-title">① 上传试卷图片并裁切</div>', unsafe_allow_html=True)
    up = st.file_uploader("上传一张试卷照片（jpg / png），照片里可以包含手写痕迹",
                          type=["jpg", "jpeg", "png"], key="up_question")

    cropped_img: Optional[Image.Image] = None
    if up is not None:
        try:
            raw = Image.open(io.BytesIO(up.getvalue()))
            raw.load()                       # 强制解码，避免文件句柄问题
            cropped_img = image_editor(raw, "q", "确认只保留本题区域：")
            st.image(cropped_img, caption="裁切结果（将用于识别与 PDF 插图）",
                     use_container_width=True)
            st.checkbox(
                "PDF 第 1 页插入这张题目小图（几何题等需要看图的题目勾选，纯文字题可不勾）",
                value=False, key="insert_q_img")
        except Exception as exc:  # noqa: BLE001
            st.error(f"图片读取失败，请换一张图片重试：{exc}")

    # ------------------------------------------------------------------
    # 第二块：VL 识别 → 纯文字题干（可编辑）
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown('<div class="section-title">② 识别题目文字</div>', unsafe_allow_html=True)

    btn_col, tip_col = st.columns([1, 2])
    with btn_col:
        recog = st.button("识别题目（忽略手写）", type="primary",
                          disabled=(cropped_img is None), use_container_width=True)
    with tip_col:
        st.markdown('<div class="small-tip">只识别印刷体原题，自动忽略手写答案与草稿；'
                    '几何题会把图形条件写成文字。</div>', unsafe_allow_html=True)

    if recog and cropped_img is not None:
        with st.spinner("正在识别题目，请稍候…"):
            try:
                st.session_state.question_text = _recognize_question_vl(
                    cropped_img, st.session_state.api_key)
            except Exception as exc:  # noqa: BLE001
                st.error(f"识别失败：{exc}")

    question_text = st.text_area(
        "纯文字题干（识别有误可直接修改，这里的内容会原样进入 PDF 第 1 页）",
        value=st.session_state.question_text,
        height=140,
        key="ta_question",
    )
    # 用户在文本框里的修改即时生效
    st.session_state.question_text = question_text

    # ------------------------------------------------------------------
    # 第三块：AI 生成 + 自动复核
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown('<div class="section-title">③ 生成解析与变式题（含自动复核）</div>',
                unsafe_allow_html=True)

    if st.button("生成 AI 解析 + 3 道变式题", type="primary",
                 disabled=not question_text.strip(), use_container_width=True):
        with st.spinner("第 1 步 / 共 2 步：正在生成解析与变式题…"):
            try:
                raw_md = client.generate_solution(question_text.strip())
            except Exception as exc:  # noqa: BLE001
                st.error(f"生成失败：{exc}")
                raw_md = ""

        if raw_md:
            bundle = parse_solution_bundle(raw_md)
            st.session_state.analysis = bundle["analysis"]
            st.session_state.variants = bundle["variants"]
            st.session_state.variant_solutions = bundle["variant_solutions"]

            with st.spinner("第 2 步 / 共 2 步：正在复核解析（验算计算与逻辑）…"):
                try:
                    review = client.review_solution(question_text.strip(),
                                                    st.session_state.analysis)
                except Exception as exc:  # noqa: BLE001
                    review = ""
                    st.error(f"复核失败：{exc}")

            st.session_state.review_text = review
            if review:
                passed, fixed = parse_review_result(review)
                st.session_state.review_passed = passed
                st.session_state.fixed_analysis = fixed
            else:
                st.session_state.review_passed = None
                st.session_state.fixed_analysis = ""
            st.rerun()

    # ---- 3.1 原始 AI 解析 ----
    if st.session_state.analysis:
        st.markdown("**原始 AI 解析**")
        show_md("原始 AI 解析", st.session_state.analysis, "analysis")

    # ---- 3.2 自动复核结果（单独一块，绿=通过，红=发现错误）----
    if st.session_state.review_text:
        st.markdown("**自动复核结果**")
        passed = st.session_state.review_passed
        if passed is True:
            st.success("✅ 解析校验通过 —— 复核未发现计算、逻辑或公式错误。")
        else:
            st.error("⚠️ 复核发现问题 —— 请查看下方说明，并人工核对后再使用。")
            pos = extract_error_position(st.session_state.review_text)
            if pos:
                st.warning(f"错误位置：{pos}")
        show_md("复核结果", st.session_state.review_text, "review")

        if passed is not True and st.session_state.fixed_analysis:
            choice = st.radio(
                "PDF 第 2 页左侧使用哪一版解析？",
                options=["修正后解析（推荐）", "原始解析"],
                index=0,
                horizontal=True,
                key="rb_final",
            )
            st.session_state.final_choice = "fixed" if choice.startswith("修正") else "raw"

    def final_analysis() -> str:
        """确定写入 PDF 的最终解析：修正版优先（若用户选择），否则原始解析。"""
        if (st.session_state.final_choice == "fixed"
                and st.session_state.fixed_analysis):
            return st.session_state.fixed_analysis
        return st.session_state.analysis

    # ---- 3.3 变式题 + 解析 ----
    if st.session_state.variants:
        st.markdown("**3 道变式练习（只放题目，PDF 第 3 页）**")
        show_md("变式题", st.session_state.variants, "variants")
    if st.session_state.variant_solutions:
        st.markdown("**变式题解析（PDF 第 4 页起）**")
        show_md("变式解析", st.session_state.variant_solutions, "v_sol")

    # ------------------------------------------------------------------
    # 第四块：手写解题图（放在 PDF 第 2 页右侧 30%）
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown('<div class="section-title">④ 上传手写解题步骤（可选）</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="small-tip">这张图会放在 PDF 第 2 页右侧 30% 的「我的解题过程 / 补充」区域；'
                '不上传时该区域留白，方便打印后手写。</div>', unsafe_allow_html=True)

    hw_up = st.file_uploader("上传手写解题照片（jpg / png）",
                             type=["jpg", "jpeg", "png"], key="up_hand")
    hw_img: Optional[Image.Image] = None
    if hw_up is not None:
        try:
            hw_raw = Image.open(io.BytesIO(hw_up.getvalue()))
            hw_raw.load()
            hw_img = image_editor(hw_raw, "h", "可旋转与裁切手写图：")
            st.image(hw_img, caption="手写图预览（将缩放到 PDF 右侧 30% 区域）",
                     use_container_width=True)
        except Exception as exc:  # noqa: BLE001
            st.error(f"手写图读取失败：{exc}")

    # ------------------------------------------------------------------
    # 预览 / 导出
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown('<div class="section-title">⑤ 预览与导出</div>', unsafe_allow_html=True)

    c_prev, c_pdf = st.columns(2)

    with c_prev:
        if st.button("预览卡片", use_container_width=True,
                     disabled=not st.session_state.question_text.strip()):
            st.session_state.show_preview = not st.session_state.show_preview
            st.rerun()

    with c_pdf:
        if st.button("导出 PDF", type="primary", use_container_width=True,
                     disabled=not st.session_state.question_text.strip()):
            q_img = cropped_img if st.session_state.get("insert_q_img", False) else None
            try:
                with st.spinner("正在按模板生成 PDF…"):
                    pdf_bytes = build_card_pdf(
                        question_text=st.session_state.question_text.strip(),
                        question_image=q_img,
                        analysis_text=final_analysis(),
                        handwriting_image=hw_img,
                        variants_text=st.session_state.variants,
                        variant_solutions_text=st.session_state.variant_solutions,
                    )
                st.session_state.pdf_bytes = pdf_bytes
                st.success("PDF 已生成，点击下方按钮下载。")
            except Exception as exc:  # noqa: BLE001
                st.error(f"PDF 生成失败：{exc}")

    if st.session_state.get("pdf_bytes"):
        st.download_button(
            "⬇️ 下载错题卡片 PDF",
            data=st.session_state.pdf_bytes,
            file_name="错题卡片.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    # ---- 卡片预览（贴近 PDF 版式：解析页 70% / 30%）----
    if st.session_state.show_preview:
        st.markdown("---")
        st.markdown("**卡片预览**")
        st.markdown("##### 第 1 页 · 错题卡片")
        st.markdown(st.session_state.question_text or "（题干为空）")
        if st.session_state.get("insert_q_img", False) and cropped_img is not None:
            st.image(cropped_img, width=320)

        st.markdown("##### 第 2 页 · 解析（左侧 70% / 右侧 30%）")
        left, right = st.columns([7, 3])
        with left:
            st.markdown("**AI 解析（已复核）**")
            st.markdown(final_analysis() or "（暂无解析）")
        with right:
            st.markdown("**我的解题过程 / 补充**")
            if hw_img is not None:
                st.image(hw_img, use_container_width=True)
            else:
                st.markdown('<div class="small-tip">（留白，打印后手写填写）</div>',
                            unsafe_allow_html=True)

        st.markdown("##### 第 3 页 · 同类变式练习")
        st.markdown(st.session_state.variants or "（暂无变式题）")

        st.markdown("##### 第 4 页起 · 变式题解析")
        st.markdown(st.session_state.variant_solutions or "（暂无解析）")

    # ------------------------------------------------------------------
    # 页脚提示（需求指定，必须写在页面底部）
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown(FOOTER_TIP)
    st.markdown('<div class="small-tip">PDF 字体说明：程序会自动探测系统自带中文字体；'
                '若目标电脑缺少中文字体，可能回退为宋体 CID 字体，显示略有差异。</div>',
                unsafe_allow_html=True)


# ===========================================================================
# 入口：未验证密钥 → 验证页；已验证 → 主功能页
# ===========================================================================

def main() -> None:
    # 预热字体（只探测一次，结果缓存在模块内）
    try:
        get_font_names()
    except Exception:  # noqa: BLE001
        pass

    if not st.session_state.authed or st.session_state.client is None:
        page_auth()
    else:
        page_main()


if __name__ == "__main__":
    main()
