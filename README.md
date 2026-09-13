# 🕊️ HymnCrawler · 真耶穌教會聖樂网数据采集与处理流水线

一个系统化的 **TJC 赞美诗（Hymn）数据爬虫与数据处理流水线**，自动抓取真耶穌教會聖樂网（sacredmusic.tjc.org.tw）的诗歌资源，完成 **探测 → 下载 → 提取 → 转图 → 校验 → 入库** 全流程，最终沉淀为结构化的 SQLite 数据库与本地多媒体资源库。

> **当前架构（2026-09-12 重构）**：默认走**官网 JSON API**（纯 `requests`，零浏览器依赖）——元数据 + 资源 URL 一次拿全，全量 474 首 **Step 1 ≈ 6 s / Step 2 ≈ 60 s**；重构前的 **Selenium/DOM 实现完整保留**为保底引擎（`crawler_core/selenium_legacy/`，独立入口 `crawler_selenium.py`），API 异常时可整体回退。
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
│   ├── db.py                   # 数据库管理 + 迁移（v7）+ UPSERT + hymn_category 重建
│   └── selenium_legacy/        # 🛟 Selenium 保底引擎（旧实现原样保留，默认不参与主流程）
│       ├── driver.py / scanner_selenium.py / extractor_dom.py / probe_audio.py
│       └── README.md           # 用途 / 启停方法 / 何时该用
│
├── crawler_fast.py             # 🎮 统一菜单入口（API 主路径；--engine api|selenium|auto）
├── crawler_selenium.py         # 🛟 Selenium 保底独立整链入口（等价重构前行为）
├── tool/                       # 🛠 数据处理工具
│   ├── data_audit/             # 🔍 数据审计脚本（重复文件对账 / 删除前核验 / 音频对账 / 清理执行 + README）
│   ├── qwen_ocr.py             # 千问 Qwen-VL 图片 OCR 识别
│   ├── ocr_merged_slices.py    # OCR 合并切片
│   ├── merge_ocr_results.py    # OCR 结果合并
│   ├── json_to_db.py           # JSON → SQLite 入库
│   ├── merge_hymns_images.py   # 诗歌图片合并
│   ├── verify_merged.py        # 合并数据校验
│   └── ...                     # 其他数据比对 / 清理脚本
│
├── test/                       # 🧪 pytest 测试（81 项，离线可跑）
│   ├── test_api_client.py      # ★ API 客户端：分页/映射/目录名规则/重试/缓存/可用性状态机
│   ├── test_db_v7.py           # ★ DB v7：api_raw/空值守卫/hymn_category/增量计划/引擎隔离
│   ├── test_no_crash.py        # ★ 不崩断言（§5.9.6）：坏 URL/断网续跑/null 不下载/结构改版
│   ├── test_lyrics_api.py      # 歌词 API + DOM box 归并 + chorus 字段
│   └── test_smoke.py           # 纯函数冒烟
│
├── hooks/
│   └── pre-commit              # 提交前重建 checksums
│
├── docs/
│   ├── API_REFACTOR_PLAN.md    # 📐 API 重构方案（v1.3 已实施，含取证数据/决策定稿/验收结果）
│   ├── SESSION_SUMMARY.md      # 📋 开发会话总结（新会话必读）
│   ├── CLINE_CONTEXT_MINIMIZE.md # 上下文最小化指南
│   └── sessions/               # 🗂 会话开发日志档案（模板 + 按时间命名）
│
├── tjc_hymn.db                 # 🗄 SQLite 数据库（v7 结构，474 首，含 api_raw 原始记录）
├── probe_report.json           # 📋 资源探测清单（474 首；含 _http_status/_unavailable 元信息）
├── final_report.txt            # 📊 最终执行统计报告
├── requirements.txt            # 主依赖（纯 API，无 selenium）
├── requirements-selenium.txt   # 保底引擎依赖（selenium + Chrome/chromedriver）
├── ruff.toml / pytest.ini      # 门禁与测试配置
└── hymn_crawler_plan.md        # 📖 开发计划文档
```

> 📦 `Hymn_Downloads/api_cache/`：48 页 API 响应落盘（≈2.9 MB，**纳入 git 跟踪**）——离线对账/复现用；命中缓存时 Step 1 仅需 0.0 s。

---

## 🚀 快速开始

### 环境前置

- **Python 3.10+**
- 虚拟环境（推荐）：`/home/zjx/python_env/bin/python`
- 主依赖（纯 API 路径）：`pip install -r requirements.txt`（`requests` / `urllib3` / `beautifulsoup4` / `Pillow`）
- 系统工具：`poppler`（`pdftoppm` / `pdfinfo` / `pdftotext`，仅转图阶段需要）
- 保底引擎（可选）：`pip install -r requirements-selenium.txt` + Chrome/chromedriver（**默认路径不需要**）

### 运行

```bash
# 交互菜单（默认 API 引擎）
/home/zjx/python_env/bin/python crawler_fast.py

# 非交互单步（CI/脚本友好）
/home/zjx/python_env/bin/python crawler_fast.py --engine api --step 1        # Step 1 扫描
/home/zjx/python_env/bin/python crawler_fast.py --engine api --step 2        # Step 2 提取
/home/zjx/python_env/bin/python crawler_fast.py --engine api --step 3        # 资源探测
/home/zjx/python_env/bin/python crawler_fast.py --step 5                     # 校验与报告
/home/zjx/python_env/bin/python crawler_fast.py --step 10                    # 全量/增量极速同步
/home/zjx/python_env/bin/python crawler_fast.py --step check                 # 三方一致性检查（不落盘）
/home/zjx/python_env/bin/python crawler_fast.py --refresh-api-cache --step 1 # 忽略分页缓存重抓

# Selenium 保底整链（等价重构前行为；需已装 selenium）
/home/zjx/python_env/bin/python crawler_selenium.py
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

### 测试

```bash
/home/zjx/python_env/bin/python -m pytest test/ -x -q
```

代码质量检查（ruff / bandit / mypy 均已配置并通过）：

```bash
/home/zjx/python_env/bin/python -m ruff check .                      # 门禁范围见 ruff.toml（历史独立脚本已排除）
/home/zjx/python_env/bin/python -m bandit -c bandit.yaml -r crawler_core crawler_fast.py crawler_selenium.py
/home/zjx/python_env/bin/python -m mypy crawler_core crawler_fast.py crawler_selenium.py
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
| **校验报告** | `crawler_core/verify.py` | 三方对账（DB / 目录 / url_map）+ 资源核验 + `_unavailable` 归档 + `final_report.txt` | ✅ 完成 |
| **增量同步** | `crawler_core/sync.py` | 以 `api_raw.updated_at` 为水位的差异报表 + 落库 + `hymn_category` 重建（菜单 10 增量模式） | ✅ 完成 |
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
| `verse_count` | INTEGER | 歌词总节数（正歌节数，不含副歌） |
| `verse_1` ~ `verse_10` | TEXT | 正歌歌词内容（按实际填充，默认为空） |
| `chorus` | TEXT | **副歌**（官网 `lyrics_chorus`；v6 新增，此前因采集缺陷整段丢失） |
| `staff_img_path` | TEXT | **五线谱 PDF** 相对路径 |
| `numbered_img_path` | TEXT | **简谱 PDF** 相对路径 |
| `audio_versions` | TEXT | JSON：**版本名 → 相对路径**（如 `{"鋼琴版": "..."}`） |
| `audio_version_list` | TEXT | JSON：纯版本名列表 |
| `staff_png_path` / `numbered_png_path` | TEXT | **五线谱 / 简谱 PNG** 相对路径（第五阶段新增） |
| `download_status` | TEXT | `completed` / `partial(x/y)` / `failed` / `pending` / `dir_missing` / `no_files` |
| `integrity_status` | TEXT | `passed` / `failed` / `unchecked` |
| `api_raw` | TEXT | **API 原始记录 JSON**（v7 新增）：`category` / `tags` / `youtube_urls` / `updated_at` / `prev_no` / `next_no` / `history` HTML 等；读取用 `api_client.api_field(raw, "category.name")` 等助手 |
| `updated_at` | TIMESTAMP | 更新时间（本地时间 CST） |

> 🔄 **自动迁移**：`crawler_core/db.py` 的 `init_db()` 支持从任意旧版本自动升级（v1 → v7），无需手动干预。
> 📚 `hymn_category` 表由 `db.rebuild_hymn_category(records)` **用 API 重建**（`id / name / slug / hymn_count / updated_at`，先建临时表再原子替换，失败回滚、可重复执行）。
> 🔁 **UPSERT 原则（v6 起）**：`title` / 作者 / 源考 / 副歌 / 路径 / 状态 / `api_raw` 等字段**空值不覆盖旧值**——API 侧缺失时保留库内既有成果（如 #25/#31/#66/#299 官网无 lyricists、#349 无 history）。

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
- **资源可用性语义（v7 起）**：不可用资源（`file_url=null` 空记录 / HTTP 4xx / 重试后仍网络失败）**不计入期望集合**，
  其 `url` 置 `None` 并在 `probe_report.json` 中带 `_unavailable` / `_http_status` / `_url` 留痕 → 不下载、不重试、不算缺失。
  当前 10 条：9 条空记录（#178、#249、#255、#268、#274_b、#308、#386、#387、#389）+ **#62 人聲版 404**；
  `verify.py` 会据此生成"失败任务归档"章节（不再硬编码个案），#62 的 `download_status` 已归正为 `completed`。
- **#349 特别说明**：官网已把 349 号整首诗由《救主正在等待》换为《奇妙的耶穌》（旧直链已 404），
 旧 5 个资源（2 PDF + 3 音频）保留在 `Hymn_Downloads/354_349奇妙的耶穌/_archive/`（含 README + md5），
 当前资源按 API 新 URL 重新下载；该目录不计入 `verify` 统计。
- 数据审计脚本：`tool/data_audit/`（重复文件对账 / 删除前核验 / 终版音频对账 / 清理执行），复检顺序见其 README。
