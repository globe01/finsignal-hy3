# 环境配置与换机复现指南

本文档用于在新电脑上复现 FinSignal-Hy3 的开发与验证环境。仓库不提交 `.venv`、`.env`、原始年报 PDF 和本地运行产物；换机时按本文重新安装依赖、放回本地数据即可。

## 1. 基础要求

- Python 3.10 或以上；推荐使用 Python 3.11/3.12/3.13。
- Git。
- 可选：Hy3 / 腾讯云 TokenHub API Key。离线评测、真实数据窗口规划和 PDF 提取不需要 Key；调用 Hy3 生成分析卡片或启用 Hy3-as-Judge 时才需要。

## 2. 克隆仓库

```bash
git clone https://github.com/globe01/finsignal-hy3.git
cd finsignal-hy3
```

如果已经有本地仓库：

```bash
cd /path/to/finsignal-hy3
git pull origin main
```

## 3. 创建虚拟环境

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果遇到 pip cache 权限警告，可改用：

```bash
python -m pip install --no-cache-dir -r requirements.txt
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

若 PowerShell 阻止脚本执行，可在当前终端临时放开：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 4. 配置 Hy3 环境变量

复制示例配置：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```env
HY3_BASE_URL=https://tokenhub.tencentmaas.com/v1
HY3_API_KEY=your-api-key
HY3_MODEL=hy3
HY3_SEND_SAMPLING_PARAMS=0
HY3_TEMPERATURE=0.2
HY3_TOP_P=1.0
HY3_TIMEOUT_SECONDS=120
HY3_REASONING_EFFORT=high
```

注意：

- `.env` 只保留在本地，不要提交。
- 当前 Phase 3 真实年报结构化和 `eval/run_real_eval.py --list` 不调用 Hy3，可以暂时不填 Key。
- 若腾讯云控制台示例中的 `base_url` 或 `model` 与 `.env.example` 不同，以控制台为准。

## 5. 放置原始年报 PDF

原始 PDF 不提交 GitHub。换电脑后，需要自行把本地 `data/raw/` 放回项目目录，目录形如：

```text
data/raw/cninfo/
├── 002594_BYD/
├── 300750_CATL/
├── 000651_GREE/
├── 601012_LONGI/
├── 600031_SANY/
├── 600309_WANHUA/
├── 000063_ZTE/
└── 600276_HENGRUI/
```

`data/raw/` 已在 `.gitignore` 中忽略。仓库只提交 `data/derived/manifest.csv` 和 `data/derived/real_financials_2021_2025.csv` 等派生数据。

## 6. 验证环境

### 离线评测与单测

```bash
python -m eval.run_eval --offline
python -m pytest -q
```

### 真实样本窗口规划

```bash
python -m eval.run_real_eval --list
```

预期能看到 8 家公司、24 个三年窗口；窗口内字段齐全显示 `READY`，含 N/A 字段显示 `PARTIAL`（当前 **17 READY + 7 PARTIAL**，无 `PENDING` 窗口——8 家年报均已结构化摘录）。

### PDF 提取脚本

需要本地存在对应年报 PDF，并且已安装 `pdfplumber`：

```bash
python scripts/extract_byd_financials.py
python scripts/extract_gree_financials.py
python scripts/extract_longi_financials.py
python scripts/extract_catl_financials.py
```

脚本会更新 `data/derived/real_financials_2021_2025.csv` 中对应公司的结构化字段。运行后建议检查：

```bash
git diff --check
python -m eval.run_real_eval --list
```

## 7. 常见问题

### `ModuleNotFoundError: No module named 'pdfplumber'`

说明当前终端没有激活虚拟环境，或依赖没装完整：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -c "import pdfplumber; print(pdfplumber.__version__)"
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -c "import pdfplumber; print(pdfplumber.__version__)"
```

### `HY3_API_KEY` 相关报错

离线命令不需要 Key。只有运行在线评测、Streamlit Demo 调 Hy3、或 Hy3-as-Judge 时才需要：

```bash
python -m eval.run_eval --limit 3
python -m eval.run_eval --limit 3 --hy3-judge
streamlit run app/streamlit_app.py
```

Streamlit Demo 支持四种输入方式：选择已结构化的真实公司样本、上传 CSV/Excel、在线表格录入、粘贴文本；
未配置 Key 时仍可浏览界面，点击「运行扫描」会提示配置 `.env`。

若腾讯云欠费、Key 过期或模型名变化，先更新 `.env`，不要把 Key 写进代码或提交到仓库。

### 不小心生成了本地运行产物

以下内容通常不需要提交：

- `.venv/`
- `.env`
- `data/raw/`
- `results/real_eval/`
- `__pycache__/`
- `.DS_Store`

提交前检查：

```bash
git status --short
git diff --check
```
