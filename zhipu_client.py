# -*- coding: utf-8 -*-
"""
zhipu_client.py —— 智谱 GLM（OpenAI 兼容接口）调用封装

模型分工（硬约束）：
  · glm-4.1v-thinking-flash ：多模态看图，识别试卷中的印刷体数学原题
  · glm-4-flash             ：纯文本生成（AI 解析 + 变式题）与自动复核

安全约定：
  · API Key 只由调用方（Streamlit 会话）传入，本模块不做任何持久化；
  · 不写文件、不落数据库、不上传任何第三方服务器，只与 open.bigmodel.cn 通信；
  · 所有异常统一转成中文友好提示，避免把原始堆栈暴露给中学生用户。
"""

from __future__ import annotations

import base64
import io
from typing import Optional, Tuple

from PIL import Image

# ---------------------------------------------------------------------------
# 常量：接口地址与模型名（代码内只留占位与常量，绝不硬编码 API Key）
# ---------------------------------------------------------------------------
BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
VL_MODEL = "glm-4.1v-thinking-flash"   # 看图识题（多模态）
TEXT_MODEL = "glm-4-flash"             # 文本生成 + 自动复核

# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

# 看图识题：只认印刷体原题，忽略一切手写内容
VL_PROMPT = """你是数学试卷识别助手。请只识别图片中的【印刷体数学原题】。

严格要求：
1. 只输出题目本身（题号、题干、选项、已知条件、要求证明/求解的内容）。
2. 完全忽略所有手写答案、手写草稿、手写笔记、勾画、批注、涂改痕迹。
3. 如果是几何题或含图形的题，请在文字中把图形的关键条件描述清楚，
   例如「如图，在△ABC中，AB=AC，D为BC中点，∠BAC=40°」。
4. 不要输出「这张图片显示……」之类的图片描述，不要输出任何解释性前言。
5. 不要识别或复述手写内容，不要给出答案。
6. 输出格式：干净的纯文字题干，可直接抄写，公式用常规数学写法（如 x^2、√3、∠A）。

现在请输出识别到的题目文字："""

# 第一轮生成：解析 + 3 道变式题 + 变式题解析
SOLVE_PROMPT = """你是资深中学数学教师。下面给出一道数学题的纯文字题干，请你完成三项任务。

【题干】
{question}

【任务一】给出这道题的详细解析，要求：
- 先说考查的知识点与解题突破口；
- 再按步骤推导，每一步写清依据（公式、定理、性质）；
- 最后给出明确结论；
- 末尾用一两句话点出「易错点」或「方法总结」。

【任务二】编写 3 道【同考点、难度接近】的变式练习题。要求：
- 只写题目，不要在这部分给出答案；
- 三道题数据不同、情境略有变化，但考点一致；
- 用 1. 2. 3. 编号，题目之间空一行。

【任务三】给出【任务二】3 道变式题各自的详细解析，用 1. 2. 3. 编号，与题目顺序一一对应。

【输出格式】严格使用下面三个 Markdown 小节标题，不要增删标题，不要输出多余前言：

## 一、AI详细解析
（这里写详细解析）

## 二、变式练习（3道）
1. ...
2. ...
3. ...

## 三、变式题解析
1. ...
2. ...
3. ...

注意：数学公式尽量写成一行可读的形式，例如 x^2 + 2x + 1 = 0、√3/2、∠ABC = 60°、a₁ = 2。"""

# 自动复核：把题干 + 解析再喂给模型验算
REVIEW_PROMPT = """你是数学阅卷复核老师。下面给出一道数学题的题干，以及一份 AI 生成的解析。
请你严格验算：计算是否准确、逻辑是否成立、公式与定理使用是否正确、结论是否可靠。

【题干】
{question}

【待复核的解析】
{analysis}

【输出要求】
- 如果全部正确：只输出一行「【解析校验通过】」，可另附一句简要确认说明。
- 如果发现错误：按下面格式输出：
  [校验结论] 发现错误
  [错误位置] 写明第几步 / 哪个式子出错，错在哪里
  [修正后解析] 给出修正后的完整解析（可直接替换原解析）

注意：请务必真的动手验算，不要敷衍；若无把握判定，请明确指出「此处存疑，建议人工核对」。
现在开始复核："""


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------

class ZhipuClient:
    """智谱 GLM 的轻量封装：连通测试 / 看图识题 / 文本生成 / 自动复核。"""

    def __init__(self, api_key: str, timeout: int = 180):
        """
        :param api_key: 用户在网页上输入的 API Key（仅存活于内存）
        :param timeout: 单次请求超时秒数（VL 模型较慢，给足时间）
        """
        from openai import OpenAI  # 延迟导入，报错信息更友好
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.client = OpenAI(api_key=self.api_key, base_url=BASE_URL, timeout=timeout)

    # ---------- 内部：统一异常翻译 ----------
    @staticmethod
    def _translate_error(exc: Exception) -> str:
        """把 openai SDK 的异常翻译成中学生能看懂的中文提示。"""
        name = type(exc).__name__
        msg = str(exc)
        if name == "AuthenticationError" or "401" in msg or "鉴权" in msg or "Unauthorized" in msg:
            return "密钥错误：API Key 无效或已失效，请到智谱开放平台核对后重新输入。"
        if name == "RateLimitError" or "429" in msg:
            return "请求过于频繁：已被限流，请稍等 30 秒后再试（免费模型有并发与频率限制）。"
        if name in ("APIConnectionError", "APITimeoutError") or "timed out" in msg.lower():
            return "网络问题：无法连接智谱接口，请检查网络或代理设置后重试。"
        if "insufficient" in msg.lower() or "quota" in msg.lower() or "余额" in msg or "额度" in msg:
            return "额度耗尽：账号余额或免费额度不足，请到控制台充值或领取额度。"
        if "BadRequest" in name or "400" in msg:
            return f"请求被拒绝（可能是图片过大或内容超限）：{msg[:120]}"
        return f"调用失败：{msg[:200]}"

    def _chat(self, model: str, messages: list, temperature: float = 0.6,
              max_tokens: int = 4096) -> str:
        """统一的对话调用，返回文本内容；出错时抛出带中文说明的 RuntimeError。"""
        try:
            resp = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as exc:  # noqa: BLE001 - 统一转成友好提示
            raise RuntimeError(self._translate_error(exc)) from exc

    # ---------- 功能一：连通测试 ----------
    def test_connection(self) -> Tuple[bool, str]:
        """
        用最小代价验证密钥是否有效（只发一句极短的话，几乎不消耗额度）。
        :return: (是否成功, 提示文字)
        """
        if not self.api_key:
            return False, "请先输入 API Key"
        try:
            self._chat(TEXT_MODEL, [{"role": "user", "content": "回复：ok"}],
                       temperature=0.1, max_tokens=8)
            return True, "密钥验证通过"
        except RuntimeError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, self._translate_error(exc)

    # ---------- 功能二：看图识题 ----------
    def recognize_question(self, image: Image.Image) -> str:
        """
        调用多模态模型识别题目，返回纯文字题干。
        :param image: PIL 图片（建议已在网页端裁切到只含本题）
        """
        data_url = self._to_data_url(image)
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": VL_PROMPT},
            ],
        }]
        return self._chat(VL_MODEL, messages, temperature=0.2, max_tokens=2048)

    # ---------- 功能三：生成解析 + 变式题 ----------
    def generate_solution(self, question: str) -> str:
        """根据纯文字题干，生成解析 + 3 道变式题 + 变式解析（Markdown）。"""
        messages = [
            {"role": "system", "content": "你是严谨的中学数学教师，输出规范 Markdown。"},
            {"role": "user", "content": SOLVE_PROMPT.format(question=question)},
        ]
        return self._chat(TEXT_MODEL, messages, temperature=0.6, max_tokens=4096)

    # ---------- 功能四：自动复核 ----------
    def review_solution(self, question: str, analysis: str) -> str:
        """把题干 + 解析再提交一次，做计算/逻辑/公式校验。"""
        messages = [
            {"role": "system", "content": "你是严格的数学阅卷复核老师，必须真实验算。"},
            {"role": "user", "content": REVIEW_PROMPT.format(
                question=question, analysis=analysis)},
        ]
        return self._chat(TEXT_MODEL, messages, temperature=0.2, max_tokens=4096)

    # ---------- 工具：图片 → data URL ----------
    @staticmethod
    def _to_data_url(image: Image.Image, max_side: int = 1600) -> str:
        """
        PIL 图转 base64 data URL。
        同时做两件事：统一 RGB（避免 RGBA 报错）、限制最长边（避免请求体过大被拒）。
        """
        im = image.convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:  # 只缩小，不放大
            scale = max_side / float(max(w, h))
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)  # JPEG 体积远小于 PNG，上传更快
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"


def make_demo_client() -> Optional["ZhipuClient"]:
    """占位工厂：不做任何密钥硬编码，始终返回 None（保留以便单元测试扩展）。"""
    return None
