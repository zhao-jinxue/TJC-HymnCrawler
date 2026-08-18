# 🕊️ HymnCrawler · 真耶穌教會聖樂网数据采集与处理流水线

一个系统化的 **TJC 赞美诗（Hymn）数据爬虫与数据处理流水线**，基于 **Python + Selenium** 构建，自动抓取真耶穌教會聖樂网（sacredmusic.tjc.org.tw）的诗歌资源，完成 **探测 → 下载 → 提取 → 转图 → 校验 → 入库** 全流程，最终沉淀为结构化的 SQLite 数据库与本地多媒体资源库。

> **当前实测**：成功采集 **474 首**诗歌（含 `51_a`/`51_b` 等同名变体），数据库 `tjc_hymn.db` 完整落库。

---

## 📁 项目结构

```text
hymn_crawler/
├── crawler_core/               # 🚀 核心代码包（10 模块）
│   ├── config.py               # 全局配置
│   ├── driver.py               # Selenium 驱动管理
│   ├── scanner.py              # Step 1：扫描列表页 + 创建目录
│   ├── extractor.py            # Step 2：提取详情页文本（断点续爬）
│   ├── probe.py                # 资源探测（PDF HEAD + 音频页面点击捕获）
│   ├── downloader.py           # 资源下载 + 完整性校验 + 路径回写
│   ├── verify.py               # 数据校验与报告生成
│   ├── images.py               # PDF → 窄边距 PNG + 双页拼接
│   ├── checksums.py            # checksums.json 哈希维护
│   └── db.py                   # 数据库管理 + 迁移 + UPSERT
│
├── crawler_fast.py             # 🎮 统一菜单入口（唯一主入口）
├── tool/                       # 🛠 数据处理工具
│   ├── qwen_ocr.py             # 千问 Qwen-VL 图片 OCR 识别
│   ├── ocr_merged_slices.py    # OCR 合并切片
│   ├── merge_ocr_results.py    # OCR 结果合并
│   ├── json_to_db.py           # JSON → SQLite 入库
│   ├── merge_hymns_images.py   # 诗歌图片合并
│   ├── verify_merged.py        # 合并数据校验
│   └── ...                     # 其他数据比对 / 清理脚本
│
├── test/                       # 🧪 pytest 测试（冒烟测试）
│   └── test_smoke.py
│
├── hooks/
│   └── pre-commit              # 提交前重建 checksums
│
├── docs/
│   ├── SESSION_SUMMARY.md      # 📋 开发会话总结（新会话必读）
│   ├── CLINE_CONTEXT_MINIMIZE.md # 上下文最小化指南
│   └── sessions/               # 🗂 会话开发日志档案（模板 + 按时间命名）
│
├── tjc_hymn.db                 # 🗄 SQLite 数据库（v5 结构，474 首）
├── probe_report.json           # 📋 资源探测清单（474 首，URL 对象格式）
├── final_report.txt            # 📊 最终执行统计报告
└── hymn_crawler_plan.md        # 📖 开发计划文档
```

---

## 🚀 快速开始

### 环境前置

- **Python 3.10+**
- 虚拟环境（推荐）：`/home/zjx/python_env/bin/python`
- 核心依赖：`selenium`、`beautifulsoup4`、`requests`、`Pillow`
- 系统工具：`poppler`（`pdftoppm` / `pdfinfo` / `pdftotext`）
- 浏览器：Chrome / Edge（Selenium WebDriver 需匹配）

### 运行

```bash
# 使用虚拟环境 Python 运行统一入口
/home/zjx/python_env/bin/python crawler_fast.py
```

程序启动后显示当前数据库 / 探测报告状态，并提供菜单式交互：

| 选项 | 功能 |
| --- | --- |
| `1` | 仅 Step 1：扫描列表页 + 创建目录 |
| `2` | 仅 Step 2：提取详情页文本 |
| `3` | 仅 资源探测（PDF HEAD + 音频页面点击） |
| `4` | 仅 下载多媒体资源 |
| `5` | 校验与报告（数据对账 + 资源核验） |
| `6` | 仅 转图片入库（PDF→PNG + 双页拼接 + 路径入库 + 哈希清单） |
| `7` | **全流程**（Step 1 → 2 → 探测 → 下载 → 校验 → 转图入库） |
| `8` | 补全提取失败诗歌（菜单动态出现） |
| `0` | 退出 |

> ✅ 所有阶段**支持幂等重跑**：已存在的内容自动跳过，断点进度文件（`step2/5/7_progress.json`）可续跑。

### 测试

```bash
/home/zjx/python_env/bin/python -m pytest test/ -x -q
```

代码质量检查（ruff / bandit / mypy 均已配置并通过）：

```bash
/home/zjx/python_env/bin/python -m ruff check .
/home/zjx/python_env/bin/python -m bandit -c bandit.yaml -r crawler_core
/home/zjx/python_env/bin/python -m mypy crawler_core
```

---

## ✨ 流水线各阶段

| 阶段 | 模块 / 脚本 | 说明 | 状态 |
| --- | --- | --- | --- |
| **Step 1 扫描** | `crawler_core/scanner.py` | 遍历 24 页列表页，提取 474 首元数据，创建 `序号_编号_名称` 目录 | ✅ 完成 |
| **Step 2 提取** | `crawler_core/extractor.py` | 深入详情页提取作词 / 作曲 / 源考 / 歌词，写入 SQLite（断点续爬） | ✅ 完成 |
| **资源探测** | `crawler_core/probe.py` | HEAD 探测五线谱/简谱 PDF + Selenium 点击捕获音频 URL | ✅ 完成 |
| **资源下载** | `crawler_core/downloader.py` | 10 线程并发下载 + 断点续传 + 文件完整性校验 + 路径回写 | ✅ 完成 |
| **校验报告** | `crawler_core/verify.py` | 三方对账（DB / 目录 / url_map）+ 资源核验 + `final_report.txt` | ✅ 完成 |
| **转图入库** | `crawler_core/images.py` | PDF → 300DPI 窄边距 PNG，双页上下拼接 | ✅ 完成 |
| **哈希清单** | `crawler_core/checksums.py` | 各目录 `checksums.json` 维护 PNG SHA-256 | ✅ 完成 |
| **图片入库** | `crawler_core/db.py` (`update_png_paths`) | PNG 路径以新增字段 `*_png_path` 入库，不覆盖原 PDF 路径 | ✅ 完成 |

---

## 🗄 数据库设计（`tjc_hymn` 表）

| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | INTEGER | 自增主键 |
| `hymn_number` | TEXT | 诗歌编号（网页提取，UNIQUE，含 `51_a` 变体） |
| `title` | TEXT | 诗歌名称 |
| `lyricist` / `composer` | TEXT | 作词者 / 作曲者（缺失默认 `Unknown`） |
| `source_info` | TEXT | 诗歌源考 |
| `verse_count` | INTEGER | 歌词总节数 |
| `verse_1` ~ `verse_10` | TEXT | 歌词内容（按实际填充，默认为空） |
| `staff_img_path` | TEXT | **五线谱 PDF** 相对路径 |
| `numbered_img_path` | TEXT | **简谱 PDF** 相对路径 |
| `audio_versions` | TEXT | JSON：**版本名 → 相对路径**（如 `{"鋼琴版": "..."}`） |
| `audio_version_list` | TEXT | JSON：纯版本名列表 |
| `staff_png_path` / `numbered_png_path` | TEXT | **五线谱 / 简谱 PNG** 相对路径（第五阶段新增） |
| `download_status` | TEXT | `completed` / `partial(x/y)` / `failed` / `pending` / `dir_missing` / `no_files` |
| `integrity_status` | TEXT | `passed` / `failed` / `unchecked` |
| `updated_at` | TIMESTAMP | 更新时间（本地时间 CST） |

> 🔄 **自动迁移**：`crawler_core/db.py` 的 `init_db()` 支持从任意旧版本自动升级（v1 → v5），无需手动干预。

---

## 🛠 开发工具

- **tool/qwen_ocr.py**：调用千问 Qwen-VL 视觉模型识别图片文字（OCR），API Key 从本地文件读取
- **tool/ 数据脚本**：OCR 切片合并、JSON↔DB 比对、图片合并、结果校验等
- **测试**：`test/test_smoke.py`（6 个纯单元冒烟用例，无需网络）
- **pre-commit 钩子**：提交前自动重建 `checksums.json`

更多细节见 **[hymn_crawler_plan.md](hymn_crawler_plan.md)**（开发计划 / 数据库设计 / 历史决策）与 **docs/** 目录。

---

## 🌐 VPN 访问说明

访问 sacredmusic.tjc.org.tw 下载内容需要借助 VPN（境外网络）：

1. **VPN 客户端**：本地 `tool/Clash.Verge_2.5.2_x64-setup.exe` 为当前最新版本安装包（因体积较大**不纳入版本库**，仅限本机使用）。
2. **最新版本下载**：可从官方 GitHub Releases 获取 —— <https://github.com/clash-verge-rev/clash-verge-rev/releases>
3. **注册与套餐**：VPN 服务需注册站点并充值。推荐实用站点 <https://vbzk.akkclo11.com/>，根据自身需求选择合适套餐。

---

## ⚠️ 说明

- 数据来源：真耶穌教會聖樂网（sacredmusic.tjc.org.tw），请遵守网站使用条款。
- 数据库与下载资源位于项目根目录（`tjc_hymn.db`、`Hymn_Downloads/`），DB 内存储的是**相对项目根目录**的路径。
- 已知事实：`#62 人聲版` 为服务器端 404（页面有按钮但文件缺失），已归档不重试。
