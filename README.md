# 🕊️ HymnCrawler · 真耶穌教會聖樂网数据采集与处理流水线

一个系统化的 **TJC 赞美诗（Hymn）数据爬虫与数据处理流水线**，自动抓取真耶穌教會聖樂网（sacredmusic.tjc.org.tw）的诗歌资源，完成 **探测 → 下载 → 提取 → 转图 → 校验 → 入库** 全流程，最终沉淀为结构化的 SQLite 数据库与本地多媒体资源库。

> **目录约定（2026-09-13 重排）**：项目根只保留 `README.md` / `crawler_api.py` / `tjc_hymn.db`；
> 依赖与门禁配置 → `config/`，数据产物（`probe_report.json`、`final_report.txt`）→ `data/`，
> Selenium 保底入口 → `legacy/`，下载资源与 API 缓存 → `Hymn_Downloads/`。
>
> **当前架构（2026-09-12 重构）**：默认走**官网 JSON API**（纯 `requests`，零浏览器依赖）——元数据 + 资源 URL 一次拿全，全量 474 首 **Step 1 ≈ 6 s / Step 2 ≈ 60 s**；重构前的 **Selenium/DOM 实现完整保留**为保底引擎（`crawler_core/selenium_legacy/`，独立入口 `legacy/crawler_selenium.py`），API 异常时可整体回退。
>
> **当前实测**：成功采集 **474 首**诗歌（含 `51_a`/`51_b` 等同名变体），数据库 `tjc_hymn.db` 完整落库（v7：+`api_raw`）。

---

## 📁 项目结构

```text
hymn_crawler/
├── crawler_core/               # 🚀 核心代码包
│   ├── config.py               # 全局配置（引擎/并发/重试/预检等参数，可用环境变量覆盖）
│   ├── api_client.py           # ★ 官网 JSON API 客户端（重试退避/并发/磁盘缓存/字段映射/可用性状态机）
│   ├── naming.py               # ★ 命名真源：目录名·资源文件名·音频版本名（双引擎共用）
│   ├── driver.py               # 转发层 → selenium_legacy/driver.py（旧 import 不破）
│   ├── scanner.py              # Step 1：API 列表扫描（默认）/ scan_legacy() 保底 / check_api()
│   ├── extractor.py            # Step 2：API 直出全部字段（并发）/ DOM 降级（--engine auto）
│   ├── lyrics_api.py           # 歌词刷新：薄封装 api_client（正歌/副歌 → verse_* + chorus）
│   ├── probe.py                # 资源探测：API 清单 + URL 预检（HEAD→GET Range）+ 保底点击捕获
│   ├── sync.py                 # ★ 增量同步：api_raw.updated_at 水位差异报表 + 落库
│   ├── downloader.py           # 资源下载（瞬时错误退避重试）+ 完整性校验 + 路径回写
│   ├── verify.py               # 数据校验与报告（`_unavailable` 数据驱动归档）
│   ├── images.py               # PDF → 窄边距 PNG + 双页拼接
│   ├── checksums.py            # checksums.json 哈希维护
│   ├── ppt_jianpu.py           # ★ 从《赞美诗》PPT 提取带简谱文字歌词（解析+5 项校验+报告）
│   ├── pdf_jianpu.py           # ★ 官方简谱 PDF 侧：码位→记号自举学习 + 逐字几何对位（POC）
│   ├── db.py                   # 数据库管理 + 迁移（v7 + 带简谱歌词 v8）+ UPSERT + hymn_category 重建
│   └── selenium_legacy/        # 🛟 Selenium 保底引擎（旧实现原样保留，默认不参与主流程）
│       ├── driver.py / scanner_selenium.py / extractor_dom.py / probe_audio.py
│       └── README.md           # 用途 / 启停方法 / 何时该用
│
├── crawler_api.py              # 🎮 统一主入口（API 主路径；--engine api|selenium|auto）
├── tjc_hymn.db                 # 🗄 SQLite 数据库（v7 主表 474 首 + v8 两表「带简谱文字歌词」）
│
├── config/                     # ⚙️ 依赖 + 门禁/测试配置（详见该目录 README）
│   ├── requirements.txt        # 主依赖（纯 API，无 selenium）
│   ├── requirements-selenium.txt # 保底引擎依赖（selenium + Chrome/chromedriver）
│   ├── pytest.ini              # 测试配置（selenium 标记注册）
│   ├── ruff.toml               # 风格门禁
│   └── bandit.yaml             # 安全扫描跳过项
│
├── data/                       # 📦 流水线产物（详见该目录 README）
│   ├── probe_report.json       #   资源探测清单（474 首；含 _http_status/_unavailable 元信息）
│   ├── final_report.txt        #   最终执行统计报告
│   └── jianpu_report.txt       #   带简谱歌词提取校验报告（需复核清单 + 等长统计）
│
├── legacy/                     # 🛟 保底入口（详见该目录 README）
│   └── crawler_selenium.py     #   Selenium 保底整链入口（等价重构前行为）
│
├── tool/                       # 🛠 数据处理工具
│   ├── data_audit/             # 🔍 数据审计脚本（重复文件对账 / 删除前核验 / 音频对账 / 清理执行 + README）
│   ├── show_lyrics.py          # 🔎 入库歌词复核（看某首的正歌+副歌，并与 api_raw 逐字比对）
│   ├── extract_jianpu.py       # 🎼 PPT → 带简谱文字歌词提取入库（解析/校验/报告/写库）
│   ├── show_jianpu.py          # 🎹 简谱歌词渲染复核（简谱字体+歌词字体出图，逐字对位索引）
│   ├── show_pdf_align.py       # 📐 官谱 PDF ↔ PPT 歌词逐字对位复核（对位表 + 标注图，POC）
│   ├── qwen_ocr.py             # 千问 Qwen-VL 图片 OCR 识别
│   ├── ocr_merged_slices.py    # OCR 合并切片
│   ├── merge_ocr_results.py    # OCR 结果合并
│   ├── json_to_db.py           # JSON → SQLite 入库
│   ├── merge_hymns_images.py   # 诗歌图片合并
│   ├── verify_merged.py        # 合并数据校验
│   └── ...                     # 其他数据比对 / 清理脚本
│
├── test/                       # 🧪 pytest 测试（138 项，离线可跑）
│   ├── test_api_client.py      # ★ API 客户端：分页/映射/目录名规则/重试/缓存/可用性状态机
│   ├── test_db_v7.py           # ★ DB v7：api_raw/空值守卫/hymn_category/增量计划/引擎隔离
│   ├── test_no_crash.py        # ★ 不崩断言（§5.9.6）：坏 URL/断网续跑/null 不下载/结构改版
│   ├── test_lyrics_api.py      # 歌词 API + DOM box 归并 + chorus 字段
│   ├── test_jianpu.py          # ★ PPT 带简谱歌词：字形语义/计数/解析/编号归属/DB v8 两表
│   ├── test_pdf_jianpu.py      # ★ 官谱 PDF：字符分类/同构判定/自举学习/逐字对位（合成数据 + 真实 #1）
│   └── test_smoke.py           # 纯函数冒烟
│
├── hooks/
│   └── pre-commit              # 提交前重建 checksums
│
├── docs/
│   ├── API_REFACTOR_PLAN.md    # 📐 API 重构方案（v1.3 已实施，含取证数据/决策定稿/验收结果）
│   ├── SESSION_SUMMARY.md      # 📋 开发会话总结（新会话必读）
│   ├── hymn_crawler_plan.md    # 📖 历史开发计划文档（v1.0 全流程方案）
│   ├── CLINE_CONTEXT_MINIMIZE.md # 上下文最小化指南
│   └── sessions/               # 🗂 会话开发日志档案（模板 + 按时间命名）
└── Hymn_Downloads/             # 诗歌资源（PDF/PNG/音频 + api_cache/ + url_map.txt）
```

> 📦 `Hymn_Downloads/api_cache/`：48 页 API 响应落盘（≈2.9 MB，**纳入 git 跟踪**）——离线对账/复现用；命中缓存时 Step 1 仅需 0.0 s。

---

## 🚀 快速开始

### 环境前置

- **Python 3.10+**
- 虚拟环境（推荐）：`/home/zjx/python_env/bin/python`
- 主依赖（纯 API 路径）：`pip install -r config/requirements.txt`（`requests` / `urllib3` / `beautifulsoup4` / `Pillow` / `olefile` / `opencc` / `pymupdf` / `fontTools`）
- 系统工具：`poppler`（`pdftoppm` / `pdfinfo` / `pdftotext`，仅转图阶段需要）
- 保底引擎（可选）：`pip install -r config/requirements-selenium.txt` + Chrome/chromedriver（**默认路径不需要**）

### 运行

```bash
# 交互菜单（默认 API 引擎）；所有命令都在项目根执行
/home/zjx/python_env/bin/python crawler_api.py

# 非交互单步（CI/脚本友好）
/home/zjx/python_env/bin/python crawler_api.py --engine api --step 1        # Step 1 扫描
/home/zjx/python_env/bin/python crawler_api.py --engine api --step 2        # Step 2 提取
/home/zjx/python_env/bin/python crawler_api.py --engine api --step 3        # 资源探测
/home/zjx/python_env/bin/python crawler_api.py --step 5                     # 校验与报告
/home/zjx/python_env/bin/python crawler_api.py --step 10                    # 全量/增量极速同步
/home/zjx/python_env/bin/python crawler_api.py --step check                 # 三方一致性检查（不落盘）
/home/zjx/python_env/bin/python crawler_api.py --refresh-api-cache --step 1 # 忽略分页缓存重抓

# Selenium 保底整链（等价重构前行为；需已装 selenium）
/home/zjx/python_env/bin/python legacy/crawler_selenium.py
```

| 参数 | 说明 |
| --- | --- |
| `--engine api\|selenium\|auto` | `api`（默认，零浏览器依赖）/ `selenium`（DOM 保底）/ `auto`（API 优先，逐首失败才降级 DOM） |
| `--step 1`…`10` / `check` / `incremental` | 非交互执行单步（`check` = 扫描一致性检查，`incremental` = 增量同步） |
| `--refresh-api-cache` | 忽略并重建 `Hymn_Downloads/api_cache/`（默认命中缓存不请求） |

程序启动后显示当前数据库 / 探测报告状态，并提供菜单式交互：

| 选项 | 功能 |
| --- | --- |
| `1` | 仅 Step 1：扫描列表页 + 创建目录（API，≈6 s） |
| `2` | 仅 Step 2：提取详情页文本（API 并发，≈60 s；DOM 降级见 `--engine auto`） |
| `3` | 仅 资源探测（API 清单 + URL 预检；Selenium 保底为音频点击） |
| `4` | 仅 下载多媒体资源（瞬时错误自动退避重试） |
| `5` | 校验与报告（数据对账 + 资源核验 + `_unavailable` 归档） |
| `6` | 仅 转图片入库（PDF→PNG + 双页拼接 + 路径入库 + 哈希清单） |
| `7` | **全流程**（Step 1 → 2 → 探测 → 下载 → 校验 → 转图入库） |
| `8` | 补全提取失败诗歌（菜单动态出现） |
| `9` | 歌词重抓（官网 API 全量刷新正歌 `verse_*` + 副歌 `chorus`） |
| `10` | ⚡ 极速全量同步（纯 API）：全量 / 增量（水位 = `api_raw.updated_at`） |
| `0` | 退出 |

> ✅ 所有阶段**支持幂等重跑**：已存在的内容自动跳过，断点进度文件（`step2/5/7_progress.json`、`lyrics_progress.json`）可续跑。
> 进阶用法：`python -c "from crawler_core.lyrics_api import run; run(force=True, reset=True)"` 全量重抓歌词。

### 歌词复核（人工取证）

```bash
# 看某首的正歌 + 副歌（并与 api_raw 原文逐字比对；退出码 1 = 有编号缺失/不一致）
/home/zjx/python_env/bin/python tool/show_lyrics.py 12
/home/zjx/python_env/bin/python tool/show_lyrics.py 1-20
/home/zjx/python_env/bin/python tool/show_lyrics.py --stats       # 474 首 / 有副歌 270 / 无副歌 204
/home/zjx/python_env/bin/python tool/show_lyrics.py --no-chorus   # 列出官网未提供副歌的编号
```

> 说明：副歌字段 `chorus` 来自官网 API 的 `lyrics_chorus`。**该字段为空的诗歌（当前 204 首）官网本身就无副歌**
> （列表接口与详情接口一致，且正歌文本已含「阿們，阿們，哈利路亞！」这类内置叠句），并非抓取遗漏；
> 详见 `docs/sessions/2026-09-13_12-53-00.md` 的取证结论。

### 官方简谱 PDF 逐字对位（POC）

```bash
# 用官方简谱 PDF（矢量文本、带精确坐标）给 PPT 记谱做「逐字几何对位」复核
/home/zjx/python_env/bin/python tool/show_pdf_align.py 1          # #1：对位表 + 标注图（data/pdf_align/）
/home/zjx/python_env/bin/python tool/show_pdf_align.py 1 13 334   # 多首：先跨首联合学码位映射，再逐首出表
/home/zjx/python_env/bin/python tool/show_pdf_align.py 1 --map    # 附：学到的「码位 → 记号」映射（含票数）
```

> **原理**：官方 PDF 是 iTextSharp 生成的**矢量 PDF**——音符是嵌入字体 `MMP2005` 的**文本**（28pt）、
> 歌词是中文文本（14pt），每个字符都带精确坐标（附点/减时线等小标记用字形**墨迹框**剔除）。
> 把 DB 的 `hymn_jianpu_line.notes` 与 PDF 的「码位序列」做**结构同构 + 冲突投票**对齐，
> 即可自动学出「码位 → 记号」映射（不需要人工建表）；再把歌词汉字的 x 与音符元素的 x 做最近邻，
> 输出**逐字对位 + Δ 偏差 + 一字多音标记**，并渲染成标注图供人工终审。
>
> **#1 实测**：12 个码位票数 12/12 完全一致、4 个乐句组全命中、逐字对位 45/45 在容差内（多数 Δ≤1pt）、
> 字数校验 4/4 通过；叠加坐标后红点落在官方简谱音符上。
>
> **已知边界（POC，未落库）**：474 份中 473 份含 MMP2005 与文本层（唯一例外 #349 疑似图片版）；
> 抽样 40 首中「有对位」的诗逐字对位 308/308 可靠、字数校验 32/32 通过，但只有 9/40 首完成结构对齐——
> 多数是 **PPT 用合成字形（1 字符/拍）而 PDF 把减时线/附点拆成独立字符**造成的粒度差异，
> 另有 #334 这类 PPT 记谱本身「音符不足」的个案。全量落地前需先做**拍位聚合/粒度归一**。

### 测试

```bash
/home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/ -x -q
```

代码质量检查（ruff / bandit / mypy 均已配置并通过）：

```bash
/home/zjx/python_env/bin/python -m ruff check --config config/ruff.toml .   # 门禁范围见 config/ruff.toml（历史独立脚本已排除）
/home/zjx/python_env/bin/python -m bandit -c config/bandit.yaml -r crawler_core crawler_api.py legacy/crawler_selenium.py
/home/zjx/python_env/bin/python -m mypy crawler_core crawler_api.py legacy/crawler_selenium.py
```

> `test/test_step1|2|3.py` 是重构前的独立 Selenium 联调脚本（需网络 + 浏览器），不参与 pytest 与 ruff 门禁；
> Selenium 保底实现的用例以 `pytest -m selenium` 单独运行（见 `crawler_core/selenium_legacy/README.md`）。

---

## ✨ 流水线各阶段

| 阶段 | 模块 / 脚本 | 说明 | 状态 |
| --- | --- | --- | --- |
| **Step 1 扫描** | `crawler_core/scanner.py` | API 列表 48 页（列表=详情）→ 474 首元数据 + `序号_编号_名称` 目录（≈6 s）；`scan_legacy()` 为 Selenium 保底 | ✅ 完成 |
| **Step 2 提取** | `crawler_core/extractor.py` | API 直出作词 / 作曲 / 源考 / 歌词 / 副歌（8 线程，≈60 s，断点续爬）；`--engine auto` 时逐首 DOM 降级 | ✅ 完成 |
| **歌词刷新** | `crawler_core/lyrics_api.py` | 官网 JSON API（`/api/hymn/{no}`）取正歌 `lyrics[]` + 副歌 `lyrics_chorus`，刷新 `verse_*` / `chorus` | ✅ 完成 |
| **资源探测** | `crawler_core/probe.py` | API 直接给出 PDF/音频 URL（音频 0 次点击）+ URL 预检（HEAD→GET Range 复测）+ `_unavailable` 分类 | ✅ 完成 |
| **资源下载** | `crawler_core/downloader.py` | 10 线程并发 + 断点续传 + 瞬时错误退避重试 + 文件完整性校验 + 路径回写 | ✅ 完成 |
| **校验报告** | `crawler_core/verify.py` | 三方对账（DB / 目录 / url_map）+ 资源核验 + `_unavailable` 归档 + `data/final_report.txt` | ✅ 完成 |
| **增量同步** | `crawler_core/sync.py` | 以 `api_raw.updated_at` 为水位的差异报表 + 落库 + `hymn_category` 重建（菜单 10 增量模式） | ✅ 完成 |
| **转图入库** | `crawler_core/images.py` | PDF → 300DPI 窄边距 PNG，双页上下拼接 | ✅ 完成 |
| **哈希清单** | `crawler_core/checksums.py` | 各目录 `checksums.json` 维护 PNG SHA-256 | ✅ 完成 |
| **图片入库** | `crawler_core/db.py` (`update_png_paths`) | PNG 路径以新增字段 `*_png_path` 入库，不覆盖原 PDF 路径 | ✅ 完成 |
| **带简谱歌词** | `crawler_core/ppt_jianpu.py` + `tool/extract_jianpu.py` | 474 份 PPT → 每节每行「简谱记号 + 歌词」；5 项校验（行配对/节号自洽/跨节曲调一致/歌词归属/音符-字数等长），写 `hymn_jianpu` + `hymn_jianpu_line` | ✅ 完成（404 首全项通过 / 70 首带复核标记） |
| **官方简谱曲谱** | `crawler_core/pdf_score.py` + `tool/build_score.py` | 官方简谱 PDF（网站标准源）→ 逐乐句「曲谱串 + 歌词 + 拍位 + 逐字对应」；写 `hymn_score` / `hymn_score_line` / `hymn_score_lyric` / `hymn_score_char` / `hymn_codepoint_map` | ✅ 完成（473 首入库 / 7914 谱行 / 逐字对位可靠 99.0%；#349 无文本层待 OCR） |

---

## 🗄 数据库设计（`tjc_hymn` 表）

| 字段名（按 `_create_table_v4` 建表顺序） | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | INTEGER | 自增主键 |
| `hymn_number` | TEXT | 诗歌编号（网页提取，UNIQUE，含 `51_a` 变体） |
| `title` | TEXT | 诗歌名称 |
| `lyricist` / `composer` | TEXT | 作词者 / 作曲者（缺失默认 `Unknown`） |
| `source_info` | TEXT | 诗歌源考 |
| `verse_count` | INTEGER | 歌词总节数（正歌节数，不含副歌） |
| `verse_1` ~ `verse_10` | TEXT | 正歌歌词内容（按实际填充，默认为空） |
| `chorus` | TEXT | **副歌**（官网 `lyrics_chorus`；v6 新增，此前因采集缺陷整段丢失） |
| `staff_img_path` | TEXT | **五线谱 PDF** 相对路径 |
| `numbered_img_path` | TEXT | **简谱 PDF** 相对路径 |
| `staff_png_path` / `numbered_png_path` | TEXT | **五线谱 / 简谱 PNG** 相对路径（v5 新增；由 `crawler_core.images` 从 PDF 转出） |
| `audio_versions` | TEXT | JSON：**版本名 → 相对路径**（如 `{"鋼琴版": "..."}`） |
| `audio_version_list` | TEXT | JSON：纯版本名列表 |
| `api_raw` | TEXT | **API 原始记录 JSON**（v7 新增）：`category` / `tags` / `youtube_urls` / `updated_at` / `prev_no` / `next_no` / `history` HTML 等；读取用 `api_client.api_field(raw, "category.name")` 等助手 |
| `download_status` | TEXT | `completed` / `partial(x/y)` / `failed` / `pending` / `dir_missing` / `no_files` |
| `integrity_status` | TEXT | `passed` / `failed` / `unchecked` |
| `updated_at` | TIMESTAMP | 更新时间（本地时间 CST） |

> 🔄 **自动迁移**：`crawler_core/db.py` 的 `init_db()` 支持从任意旧版本自动升级（v1 → v7），无需手动干预。
> 🧱 **列顺序**：库内**物理列顺序**与 `_create_table_v4` 的建表顺序一致（28 列）；旧库因 v5/v6/v7 走 `ALTER TABLE` 追加曾出现顺序错位，
> 已由 `tool/reorder_table_columns.py` 重建对齐（幂等，可随时 `--dry-run` 复核）。
> 📚 `hymn_category` 表由 `db.rebuild_hymn_category(records)` **用 API 重建**（`id / name / slug / hymn_count / updated_at`，先建临时表再原子替换，失败回滚、可重复执行）。
> 🔁 **UPSERT 原则（v6 起）**：`title` / 作者 / 源考 / 副歌 / 路径 / 状态 / `api_raw` 等字段**空值不覆盖旧值**——API 侧缺失时保留库内既有成果（如 #25/#31/#66/#299 官网无 lyricists、#349 无 history）。

---

## 🎼 带简谱文字歌词（`hymn_jianpu` / `hymn_jianpu_line`，v8）

`data/赞美诗PPT/*.ppt`（474 份，外部整理）**每张幻灯片 = 一节**，正文行序为
「标题行 →（可选）节标签 `(副歌)`/`(三)` → 简谱记号行 + 歌词行 若干对 → 节号 `k/M`」。
记号是**纯 ASCII**：`1`-`7` 为音级；`q w e t y r u`（半宽 `a d f g h j s`）等字母 =
「数字 + 减时线/八度点」的**合成字形**；`/` = 延长线；`\` = 小节线；`|` = 终止线；
零宽码位 `0 8 9 = - i k P p o` 等 = 附点/八度点/升号等叠加修饰。`data/赞美诗PPT/简谱字体/简谱字体.ttf`
（01SMN "Simple music notation"）负责把上述 ASCII 渲染成简谱——**未装字体时 PPT 里"看不到字"**。

| 表 | 粒度 | 关键列 |
| :--- | :--- | :--- |
| `hymn_jianpu` | 每份 PPT 一行（主键 `ppt_file`） | `hymn_number`（↔`tjc_hymn`，未匹配留空）、`version`（甲/乙）、`ppt_old_no`/`ppt_new_no`、`key_sig`/`time_sig`/`tempo`、`slide_count`/`chorus_slides`/`pair_count`/`note_total`、`tune_period`、`title_match`/`title_score`/`verse_match`/`verse_score`、`marker_ok`/`structure_ok`/`tune_ok`/`align_ok`、`review_reason`、`src_md5`、`extractor` |
| `hymn_jianpu_line` | 每行一条（主键 `ppt_file, stanza_no, line_no`） | `notes`（原样记号）、`lyric`（原样歌词，含作者用空格做的对位）、`verse_no`/`is_chorus`/`label`、`note_count`/`rest_count`/`syllable_count`/`count_delta`/`align_ok` |

> ❓ **为什么新建两表而不扩充 `tjc_hymn`**：一首诗天然是「节 × 行」两级、行数不定（1:N）。
> 塞进 28 列宽表只能再走 `ALTER TABLE ADD COLUMN`（重演 v5~v7 的列序错位）或塞 JSON 大字段
> （SQL 里没法做等长/归属校验）。独立两表还能同时容纳 **甲/乙两个版本**（DB 编号 `51_a`/`51_b`）。
>
> 🎯 **"曲谱配错歌词"怎么防**（入库前 5 项校验，全部落列可查）：
> ① **行级配对**：每个简谱行必须紧跟其歌词行（`structure_ok`）；
> ② **节号自洽**：每节的 `k/M` 必须与实际张数一致（`marker_ok`）；
> ③ **跨节曲调一致**：同一首诗各节的旋律签名必须相同（`tune_period`/`tune_ok`）
>    —— 现网检出 **41 首**作者记谱手误（如把 `q5812` 写成 `15812`）；
> ④ **歌词归属**：本节歌词与 DB 正歌/副歌逐字比对（覆盖率 ≥0.75），确认"这段曲谱确实配这首诗"
>    （`verse_match`/`verse_score`；PPT 为旧版编号，靠标题 + 歌词双重认定）；
> ⑤ **音符-字数等长**：`count_delta = 音符数 - 字数`（>0 = 一字多音，正常；<0 = 音符数不足，必须人工复核）。
>
> ⚠️ **逐字对位（哪个字唱哪个音）PPT 里没有严格数据保证**：作者是用空格 + 字号在幻灯片上手工对齐的。
> 因此 `notes`/`lyric` **原样保存**（含空格）以复现作者对位，并提供 `tool/show_jianpu.py`
> 出图（音符序号 + 音节序号 + em 网格）与官方简谱 PNG 并排人工终审。

---

## 🎼 官方简谱曲谱（`hymn_score*`，v9）

`Hymn_Downloads/<序号>_<编号><标题>/<编号>_简谱.pdf`（网站 sacredmusic.tjc.org.tw 的**标准源**）是
**矢量文本 PDF**：音符 = 嵌入字体 `MMP2005` 的文本（28pt）、歌词 = 中文文本（14pt），每个字符都带精确坐标。
本层以它为准抽取「曲谱 + 歌词 + 拍位 + 逐字对应」；PPT 侧（v8）降为**旁证**（提供码位学习的样本）。

> ⚠️ 目录名形如 `339_334耶穌沙崙玫瑰`：**首位是网站列表序号、第二位才是诗歌编号**
> （第 52 首起两者不再相等，如 `052_51_b萬古靈磐乙`、`474_469靈恩大會`）。
> 取文件必须按**文件名** `{编号}_简谱.pdf` —— 早期实现按目录前缀匹配，会把 #334「耶穌沙崙玫瑰」
> 解析成 #329「天父我神」（40 首抽样错配 16 首、命中 0 处）。已修，见 `pdf_jianpu.pdf_path()`。

版式（实测 #334 目视 + 坐标双证）：一首 = 若干「乐句」= 2~4 个**谱层**（各声部，y 相差 ≈24pt）
+ 下方一个「歌词块」（一谱多词，块内各行字数相同）；谱层元素 x 呈**拍位栅格**（≈21pt）。

| 表 | 粒度 | 关键列 |
| :--- | :--- | :--- |
| `hymn_score` | 每首一行 | `pdf_path`/`page_count`/`phrase_count`/`line_count`/`lyric_count`/`beat_total`/`syllable_total`/`align_ok`/`review_reason`/`extractor` |
| `hymn_score_line` | 每行谱一条 | `notes`（曲谱串，含延长线 `-`）、`notes_core`（去延长线 → 与字一一对应）、`code_seq`（无损码位）、`beat_count`、`note_count`/`hold_count`/`rest_count`、`syllable_count`、`count_delta`、`is_primary`（主旋律）|
| `hymn_score_lyric` | 每节歌词一条 | `text`、`syllable_count`、`align_ok`（是否与曲谱等长）|
| `hymn_score_char` | 每个字一条 | `syllable`、`note`、`beat`（拍位）、`delta`（几何偏差 pt）、`span`（2 = 一字多音）|
| `hymn_codepoint_map` | 每码位一条 | `MMP2005` 码位 → 记号、`votes`/`total`（置信度）、`source`（learned/manual/geometry）|

> ✅ **「节拍和歌词等长、对应得上」如何落地**：
> 主旋律层「去延长线后的元素数」`note_count` 与每节歌词字数 `syllable_count` 比对，
> `count_delta = note_count − syllable_count`：
> **0 = 一字一音、等长**；`>0` = 一字多音（正常，`hymn_score_char.span=2` 标出）；`<0` = 音符数不足（必须复核）。
> 逐字对应另有几何 Δ（pt），`Δ ≤ 8pt` 记为可靠、居中于两元素者判为一字多音。
> 一条 SQL 查全部异常：`SELECT * FROM hymn_score_line WHERE is_primary=1 AND count_delta<>0;`

> 🔍 **码位 → 记号**有两条来源：① 跨源学习（`tool/build_score.py --learn`：PDF 谱层 × PPT 记号行**整行同构**投票）；
> ② 人工种子 `pdf_score.MANUAL_SEED`（按 #334 首行的「码位序列 ↔ 渲染谱面」逐位对齐，见该常量注释）。
> 库里学到的映射优先级更高，种子只作兜底。未覆盖的码位在 `notes` 显示 `?`
> ——**只影响记号可读性，不影响拍位/字数/逐字对位**（因此不计入 `align_ok`）。
> ⚠️ 注意 PPT 一行 4 小节、官方谱一行 6 小节，**整行同构**匹配天然受限（这是覆盖率的瓶颈，
> 后续靠拍位级投票提升，见会话日志 2026-09-15_21-09-00.md 任务 2）。
> 另有极少数 PDF 无文本层（如 #349：网站单独上传的 `score/<hash>.pdf`，用 Type3 字形绘制），
> 会记 `review_reason='未识别到谱层…'` 并跳过 —— 需 OCR 补（`tool/qwen_ocr.py`）。

---

## 🛠 开发工具

- **tool/qwen_ocr.py**：调用千问 Qwen-VL 视觉模型识别图片文字（OCR），API Key 从本地文件读取
- **tool/ 数据脚本**：OCR 切片合并、JSON↔DB 比对、图片合并、结果校验等
- **tool/reorder_table_columns.py**：把 `tjc_hymn` 表的**物理列顺序**对齐 `crawler_core/db.py::_create_table_v4`
  （解析代码 DDL 为唯一权威 → 事务重建 + 逐行逐列自检；幂等，`--dry-run` 只报告不改库）
- **带简谱歌词**（`data/赞美诗PPT/` 就位时）：
  - `python tool/extract_jianpu.py --dry-run`：474 份 PPT → 出校验报告 `data/jianpu_report.txt`（不写库）
  - `python tool/extract_jianpu.py`：解析 + 写 `hymn_jianpu` / `hymn_jianpu_line`（幂等，可反复跑）
  - `python tool/show_jianpu.py 1 --map`：渲染 #1 的简谱+歌词（简谱字体出图、逐字对位索引），
    与 `Hymn_Downloads/001_1頌讚獨一真神/1_简谱.png` 官方简谱并排核对；`--review` 列被标记的行
- **官方简谱曲谱**（`Hymn_Downloads/` 就位时）：
  - `python tool/build_score.py --limit 20 --dry-run`：抽 20 首试跑（只统计，不写库）
  - `python tool/build_score.py --learn`：先学「码位 → 记号」映射（写 `hymn_codepoint_map`），再全量抽取入库
  - `python tool/build_score.py --stats`：覆盖统计（等长行 / 逐字可靠率）；`--show 334` 打印某首曲谱/歌词/逐字对应
  - `python tool/build_score.py --only 334 349`：只处理指定编号（幂等，可反复跑）
- **单文件转图**：`python -m crawler_core.images --force --pdf <PDF 相对路径>`（重画指定诗歌的简谱 / 五线谱 PNG，不触发全量扫描）
- **测试**：`/home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/ -q`（纯单元，无需网络；`test_smoke.py` 冒烟 + `test_maintenance.py` 维护工具回归）
- **pre-commit 钩子**：提交前自动重建 `checksums.json`

更多细节见 **[docs/hymn_crawler_plan.md](docs/hymn_crawler_plan.md)**（开发计划 / 数据库设计 / 历史决策）与 **docs/** 目录。

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
- **资源可用性语义（v7 起）**：不可用资源（`file_url=null` 空记录 / HTTP 4xx / 重试后仍网络失败）**不计入期望集合**，
  其 `url` 置 `None` 并在 `data/probe_report.json` 中带 `_unavailable` / `_http_status` / `_url` 留痕 → 不下载、不重试、不算缺失。
  当前 10 条：9 条空记录（#178、#249、#255、#268、#274_b、#308、#386、#387、#389）+ **#62 人聲版 404**；
  `verify.py` 会据此生成"失败任务归档"章节（不再硬编码个案），#62 的 `download_status` 已归正为 `completed`。
- **#349 特别说明**：官网已把 349 号整首诗由《救主正在等待》换为《奇妙的耶穌》（旧直链已 404），
 2026-09-13 决策：**不保留历史留档**——`_archive/`（5 个旧资源 + README + md5）已删除，
 当前资源按 API 新 URL 重新下载，并由新 PDF **重出简谱 / 五线谱 PNG**
  （`python -m crawler_core.images --force --pdf <PDF 相对路径>`：300 DPI + 40 px 窄边距裁剪）。
- 数据审计脚本：`tool/data_audit/`（重复文件对账 / 删除前核验 / 终版音频对账 / 清理执行），复检顺序见其 README。
