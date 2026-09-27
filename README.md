# 错题卡片生成器（Math Error Card）

> 上传一张试卷照片 → AI 识别印刷体原题 → 生成详细解析 + 3 道同类变式题 → 自动复核验算 → 导出可直接打印的 A4 错题卡片 PDF。

面向中学生设计的极简网页小工具：把一道错题，变成一张「正面题目、背面解析+手写、附变式练习」的学习卡片。

---

## 一、项目介绍

### 它能做什么

1. **拍照识题**：上传试卷/练习册照片，支持旋转与手动裁切，只保留这一道题的区域。
2. **只认印刷体**：调用智谱多模态模型 `glm-4.1v-thinking-flash`，自动忽略手写答案、草稿与涂改痕迹，输出干净的纯文字题干（几何题会把图形条件写成文字）。
3. **生成解析 + 变式**：调用 `glm-4-flash` 生成详细解析、3 道同考点同难度的变式练习题，以及变式题各自的解析。
4. **自动复核**：把「题干 + 解析」再提交一次让模型验算计算、逻辑与公式；通过显示绿色，发现错误显示红色并给出修正版解析。
5. **手写区留白**：可上传自己整理的手写解题照片，打印在卡片背面右侧 30% 区域；不上传则该区域留白，供手写补充。
6. **固定模板导出 PDF**：按「题目页 / 解析页（70%:30% 分栏，可跨页）/ 变式题页 / 变式解析页」的固定版式输出 A4 PDF。

### PDF 页面结构（固定模板，硬约束）

| 页面 | 内容 | 版式 |
|---|---|---|
| 第 1 页 | 标题「错题卡片」+ 纯文字题干（可勾选插入题目小图） | 整页宽，留白合理 |
| 第 2 页及以后 | 左侧 70%：AI 解析（复核后最终版）；右侧 30%：我的解题过程 / 补充 | **全程 70% / 30% 分栏，解析超长可跨页，跨页后依旧维持分栏** |
| 第 3 页 | 标题「同类变式练习」+ 3 道变式题（只放题目，不放解析） | 整页宽 |
| 第 4 页及以后 | 标题「变式题解析」+ 3 道题的详细解析 | 整页宽，不分栏，页数不限 |

### 安全与隐私

- **API Key 只保存在浏览器当前会话内存**（Streamlit `session_state`），**不写入本地文件、不存入数据库、不上传任何第三方服务器**；刷新或关闭页面后即失效，需要重新输入。
- 上传的试卷图片、手写图片只存在于内存中，用于当次识别与 PDF 生成，**不做任何持久化保存**，程序退出即清理。
- 程序只与 `open.bigmodel.cn`（智谱开放平台）通信。

---

## 二、环境安装步骤

### 1. 准备 Python

推荐 Python 3.9 ~ 3.12（Windows / macOS / Linux 均可）。

```bash
python --version     # 或 python3 --version，确认已安装
```

### 2. 创建虚拟环境（推荐）

```bash
# 进入项目目录
cd math-error-card

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows（PowerShell）：
.venv\Scripts\Activate.ps1
# macOS / Linux：
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单：

| 依赖 | 用途 |
|---|---|
| `streamlit>=1.30` | 网页界面 |
| `openai>=1.40` | 调用智谱 OpenAI 兼容接口 |
| `reportlab>=4.0` | 生成固定模板 PDF |
| `Pillow>=9.0` | 图片旋转、裁切、缩放 |

> 国内网络安装慢可临时换源：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

---

## 三、智谱开放平台注册与申请 API Key

1. 打开 **智谱开放平台**：<https://open.bigmodel.cn/>
2. 点击右上角「注册/登录」，用手机号完成注册并登录。
3. 登录后进入控制台，点击右上角头像 → **API Keys**（或直接访问 <https://open.bigmodel.cn/usercenter/apikeys>）。
4. 点击「**添加新的 API Key**」，复制生成的一串密钥（形如 `xxxxx.yyyyyyyyy`）。
   - ⚠️ 密钥只完整显示一次，请立即复制保存。
5. 本项目用到两个模型：
   - 看图识题：**`glm-4.1v-thinking-flash`**
   - 文本生成与复核：**`glm-4-flash`**
   两者在开放平台均提供免费额度，新用户注册后一般即可直接调用。
6. 回到本工具首页，粘贴密钥 → 点击「**测试连接**」→ 显示「✅ 密钥验证通过」后点击「**进入工具**」。

> 密钥一旦泄露请立即到平台删除并重新生成。本项目不会保存你的密钥。

---

## 四、运行方法

在项目目录下执行：

```bash
streamlit run app.py
```

终端会输出类似地址，浏览器一般会自动打开：

```
Local URL:  http://localhost:8501
```

使用流程：

1. **验证密钥**：粘贴 API Key → 测试连接 → 进入工具。
2. **上传试卷图片**：在「① 上传试卷图片并裁切」中选择照片；可用「逆时针/顺时针 90°」旋转，展开「手动框选裁切」拖动四个滑块，只保留本题区域。
3. **识别题目**：点击「识别题目（忽略手写）」，得到纯文字题干；如有识别错误，直接在文本框里修改（这里的内容会原样进入 PDF 第 1 页）。
4. **生成与复核**：点击「生成 AI 解析 + 3 道变式题」，程序会自动完成「生成 → 复核」两步。
5. **上传手写解题图（可选）**：在「④ 上传手写解题步骤」中上传，同样支持旋转与裁切。
6. **预览 / 导出**：点击「预览卡片」查看版式；点击「导出 PDF」后，再点击「⬇️ 下载错题卡片 PDF」保存文件，直接 A4 打印即可。

> 若需要同一局域网内的平板/手机访问，可用 `streamlit run app.py --server.address 0.0.0.0`。

---

## 五、目录结构

```
math-error-card/
├── app.py            # Streamlit 主程序（密钥验证页 + 主功能页 + 交互）
├── zhipu_client.py   # 智谱 GLM 接口封装（连通测试 / 看图识题 / 生成 / 复核）
├── pdf_export.py     # reportlab 固定模板 PDF 生成（含极简 Markdown 渲染器）
├── md_utils.py       # Markdown 文本切分与解析（解析/变式题/复核结果拆分）
├── requirements.txt  # 依赖清单
└── README.md         # 说明文档
```

---

## 六、常见问题 FAQ

**Q1：点击「测试连接」提示「密钥错误」？**
确认密钥完整复制（前后无空格、无换行），且未在开发平台被删除。重新生成一个新的 Key 再试。

**Q2：提示「网络问题：无法连接智谱接口」？**
检查本机网络与代理设置；公司/校园网络可能拦截了 `open.bigmodel.cn`，可换手机热点测试。若使用代理，请确保命令行环境也配置了代理。

**Q3：提示「额度耗尽」？**
到开放平台控制台查看余额与免费额度；`glm-4-flash` 与 `glm-4.1v-thinking-flash` 均提供免费额度，但超出后需要充值或等待额度刷新。

**Q4：提示「请求过于频繁：已被限流」（429）？**
免费模型有 QPS 与并发限制。请等待 30 秒 ~ 1 分钟后重试，**不要连续快速点击按钮**。一次「生成 + 复核」会连续发起 2 次请求，属于正常现象；短时间内大量提交会临时报错，稍等即可恢复。

**Q5：识别出的题干不对/混进了手写内容？**
先裁切，让图片里只保留本题区域（裁切后识别准确率明显提升）；识别完在文本框里手动修正即可。手写笔迹潦草、印刷体模糊或拍摄倾斜都会影响识别。

**Q6：PDF 里中文显示为方块或空白？**
程序会自动探测系统字体（Windows 微软雅黑/宋体、macOS 苹方、Linux 文泉驿/Noto）。若目标电脑缺少中文字体，会回退到 reportlab 内置宋体 CID 字体。安装任意一款中文字体即可解决。

**Q7：PDF 第 2 页右侧是空的？**
这是设计如此：未上传手写图时，右侧 30% 区域留白，方便打印后自己手写补充。上传手写照片后，图片会缩放打印在该区域。

**Q8：数学公式在 PDF 里显示为 LaTeX 源码（如 `\frac{1}{2}`）？**
PDF 由 reportlab 排版，不支持渲染复杂公式。程序已把常见符号转成 Unicode（`\times`→`×`、上标 `^2`→`²`、`\sqrt`→`√` 等）。极其复杂的公式建议导出后手工补写。

**Q9：Streamlit 启动报错 `No module named xxx`？**
说明依赖未装在当前 Python 环境里。先确认激活了虚拟环境，再执行 `pip install -r requirements.txt`。

**Q10：可以批量处理多道题吗？**
当前版本为**单题模式**，一次处理一道题，不支持批量。

---

## 七、开源协议

本项目基于 **MIT License** 开源，可自由使用、修改与分发（包括商业用途），但请保留版权与许可声明。

```
MIT License

Copyright (c) 2026 Math Error Card Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 八、免责声明

- 本项目调用的 AI 模型**存在幻觉风险**：识别出的题干、生成的解析答案、变式练习题与解析，**都可能出现错误**，请务必人工核对后再用于学习或教学。
- 本工具生成的解析与题目**仅供学习参考**，不构成任何考试、作业或教学上的准确性保证；因直接使用 AI 生成内容造成的后果由使用者自行承担。
- 使用本工具需自行申请并保管智谱开放平台 API Key，由此产生的**接口调用费用、额度消耗与限流**，由使用者自行承担。
- 使用者上传的试卷、作业图片可能包含个人信息，请自行注意隐私；本项目不做任何保存与上传，但仍建议避免在公开网络环境下处理敏感内容。

---

## 九、关于本仓库

本仓库**仅存放项目代码与文档**，不包含任何试卷、题目图片、错题素材等第三方内容。所有题目素材由使用者在本地自行上传，处理完即释放，不会被收集或分发。
