# 真耶穌教會聖樂网爬虫开发计划

## 📑 目录
- [一、项目概述](#一项目概述)
- [二、第一阶段：目录结构构建与基础索引](#二第一阶段目录结构构建与基础索引)
- [三、第二阶段：文本信息提取与数据库架构](#三第二阶段文本信息提取与数据库架构)
- [四、第三阶段：多媒体资源下载与状态追踪](#四第三阶段多媒体资源下载与状态追踪)
- [五、第四阶段：项目收尾与数据校验](#五第四阶段项目收尾与数据校验)
- [六、第五阶段：PDF 转图片与图片入库](#六第五阶段pdf-转图片与图片入库)
- [七、技术栈与环境配置](#七技术栈与环境配置)
- [八、开发注意事项](#八开发注意事项)
- [九、全局任务清单 (Checklist)](#九全局任务清单-checklist)
- [十、已知问题与待优化项](#十已知问题与待优化项)

> 📌 **文档更新说明**：本文件已同步到 **v5 实际状态**。第四阶段已完成，第五阶段（PDF→PNG 转图片 + 图片路径以新增字段方式入库）已完成，并完成全量代码静态/安全/类型检查与修复。

---

## 一、项目概述
本项目旨在系统化抓取真耶穌教會聖樂网（sacredmusic.tjc.org.tw）的 469 首诗歌资源。
项目采用**流水线多阶段架构**：抓取文本 → 探测资源 → 下载多媒体 → 校验报告 → **转换图片并入库**，确保数据采集的稳定性、结构化存储的规范性、多媒体资源的完整性以及最终数据的可追溯性。

> 📌 **当前实测数据**：实际扫描到 **474 首**诗歌（含 `51_a`/`51_b` 等同名变体），网站列表共 **24 页**，每页约 20 首。

---

## 二、第一阶段：目录结构构建与基础索引（✅ 已完成）

### 1. 核心目标
- 遍历网站列表页，获取全部诗歌的元数据。
- 在本地构建标准化的目录结构，为后续文件存储打下基础。

### 2. 关键规则
- **目录命名**：`序号(3位)_诗歌编号_诗歌名称`（例如：`001_1頌讚獨一真神`）。
- **序号规则**：从 `001` 开始升序，**诗歌编号从网页直接提取**（如 `51_a`、`124_b`）。
- **同名处理**：若出现"甲、乙"等同名诗歌，**诗歌编号保持不变**，仅通过编号后缀 `_a`/`_b` 区分。
- **a/b 变体**：DB 编号 `51_a`/`51_b` 等 10 首，目录为 `051_51_a萬古靈磐甲`（序号 051 ≠ 编号 51）。

### 3. 性能表现
- **Step 1 扫描**：24 页，474 首，总耗时约 **24 秒**，平均每页约 **1 秒**。
- **策略**：使用 Selenium + `page_load_strategy='eager'` + 禁用图片/CSS 加载。

### 4. 交付物
- `crawler_core/scanner.py`：正式扫描模块（Step 1）。
- `step1_create_dirs.py`：遗留独立脚本（保留，与 crawler_core 等价）。
- `crawler_fast.py`：统一入口（Step 1 和 Step 2 共享一次浏览器会话）。
- `test_step1.py`：测试脚本。

---

## 三、第二阶段：文本信息提取与数据库架构（✅ 已完成）

### 1. 核心目标
- 深入诗歌详情页，提取结构化文本信息。
- 构建本地 SQLite 数据库，实现数据的持久化与规范化管理。

### 2. 数据库设计 (`tjc_hymn`)（✅ v5 已更新）
- **位置**：项目根目录（`hymn_crawler/tjc_hymn.db`）。
- **引擎**：`sqlite3`。

| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | INTEGER | 自增主键 |
| `hymn_number` | TEXT | 诗歌编号（网页提取，UNIQUE） |
| `title` | TEXT | 诗歌名称 |
| `lyricist` | TEXT | 作词者（缺失默认为 `Unknown`） |
| `composer` | TEXT | 作曲者（缺失默认为 `Unknown`） |
| `source_info` | TEXT | 诗歌源考 |
| `verse_count` | INTEGER | 歌词总节数 |
| `verse_1` ~ `verse_10` | TEXT | 歌词内容（预留10字段，按实际填充，默认为空） |
| `staff_img_path` | TEXT | **五线谱 PDF 相对路径**（相对项目根目录） |
| `numbered_img_path` | TEXT | **简谱 PDF 相对路径**（相对项目根目录） |
| `audio_versions` | TEXT | **JSON 字符串**，存储**版本名 → 本地相对路径字符串** |
| `updated_at` | TIMESTAMP | 更新时间（默认本地时间 `datetime('now','localtime')`） |
| `audio_version_list` | TEXT | **JSON 字符串**，纯版本名列表（如 `["鋼琴版","人聲版"]`） |
| `staff_png_path` | TEXT | **五线谱 PNG 相对路径**（第五阶段新增字段） |
| `numbered_png_path` | TEXT | **简谱 PNG 相对路径**（第五阶段新增字段） |
| `download_status` | TEXT | 下载状态：`completed`/`partial(x/y)`/`failed`/`pending`/`dir_missing`/`no_files` |
| `integrity_status` | TEXT | 文件完整性：`passed`/`failed`/`unchecked` |

> 📌 **字段顺序（2026-08-09 调整）**：`staff_png_path` / `numbered_png_path` 位于中后部，
> `download_status` / `integrity_status` **移至所有字段末尾**（24/25 位）。
> 采用"建新表→按新顺序拷贝→替换"方式迁移，保留主键/唯一约束/默认值，474 行数据完整。

> **📌 图片路径双轨制**：
> - `staff_img_path` / `numbered_img_path`：存 **PDF 路径**（原始乐谱文件）
> - `staff_png_path` / `numbered_png_path`：存 **PNG 路径**（转图产物，供图片直接展示）
> - 二者并存，互不覆盖；PNG 由 `step7_png_db.py` 从 PDF 路径推导回填。

> **📌 `audio_versions` 字段存储结构（v4 起）**：
> ```json
> {
>   "鋼琴版": "Hymn_Downloads/002_2讚美聖父/2_鋼琴版.m4a",
>   "人聲版": "Hymn_Downloads/002_2讚美聖父/2_人聲版.mp3"
> }
> ```
> ⚠️ 存的是【版本名 → 本地相对路径字符串】，**不再是线上 URL 对象**（`probe_report.json` 里仍是 URL 对象，勿混淆）。

> **📌 历史字段说明（已弃用）**：
> 旧字段 `piano_audio_path` + `vocal_audio_path` 仅支持两种类型，已合并到 `audio_versions`。
> 首次运行 `crawler_fast.py` 时自动执行数据库迁移合并旧数据。

### 3. 数据库迁移（自动执行）
迁移逻辑在 `crawler_core/db.py` 的 `init_db()` 中自动执行，支持从任意旧版本升级：
- **v1 → v4**：存在 `piano_audio_path`/`vocal_audio_path` 无 `audio_versions` → 备份旧表→建新表→合并→回填
- **v2 → v4**：有 `audio_versions` 缺 `audio_version_list` → ADD COLUMN + 生成版本列表 + 回填
- **v3 → v4**：缺 `download_status`/`integrity_status` → ADD COLUMN
- **v4（最新）**：从 `probe_report.json` 回填空音频记录（幂等）
- **PNG 字段**：`step7_png_db.py` 幂等 ADD COLUMN `staff_png_path`/`numbered_png_path`

### 4. 性能表现
- **Step 2 提取**：474 首全部成功入库，平均每首约 **3 秒**（含页面渲染 + Tab 切换）。
- **策略**：`about:blank` → `url` 跳转触发 Vue SPA 路由；等待 `.author_name` 作为最可靠的渲染完成信号。
- **断点续爬**：`Hymn_Downloads/step2_progress.json`，每成功 1 首立即持久化；`resume=False`/`clear_progress()` 可全量重跑。

### 5. 交付物
- `crawler_core/extractor.py`：正式提取模块（Step 2 + 断点续爬）。
- `step2_extract_text.py`：遗留独立脚本（保留）。
- `crawler_core/db.py`：数据库模块，含迁移、UPSERT（CASE 保护空值不覆盖路径/状态）。
- `test_step2.py`：测试脚本。

---

## 四、第三阶段：多媒体资源下载与状态追踪（✅ 已完成）

### 1. 核心目标
- 批量探测（HEAD + Selenium）全部 474 首诗歌的 PDF 乐谱与多版本音频。
- 根据探测清单并发下载所有资源到本地诗歌目录。
- 将资源路径回写至数据库（`staff_img_path` / `numbered_img_path` / `audio_versions` JSON）。

### 2. 🔍 资源探测策略（已完成）
#### PDF 探测（多线程 HEAD，20 并发）
```
五线谱：HEAD https://sacredmusic.tjc.org.tw/storage/uploads/hymn/score/sheet/{编号}.pdf
简谱：  HEAD https://sacredmusic.tjc.org.tw/storage/uploads/hymn/score/num/{编号}.pdf
```
- **结果：474/474 五线谱 + 474/474 简谱（100%）** ✅

#### 音频探测（Selenium 页面点击捕获，4 实例并行）
- 遍历页面所有 `img.play` 按钮，过滤 `data-type` 含"讚美詩"；点击触发播放 → 读取 `audio#player.src`
- **结果：474/474 有音频（100%）** ✅（#410/#459 + 6 首 `_error` 已重新探测修复）

### 3. 音频版本分布（实测 474 首）
| 版本 | 数量 | 占比 |
| :--- | :---: | :---: |
| 鋼琴版 | 473 | 99% |
| 人聲版 | 462 | 97% |
| 四部合唱版 | 47 | 9% |
| 合唱-1/2/3/4部版 | 各 35 | 各 7% |
| 无音频 | 0 | 0% |

### 4. 下载策略
- **并发数**：10 线程并发下载；**断点续传**：跳过已存在文件
- **文件命名**：`{hymn_number}_{版本名}.{ext}`（示例：`1_鋼琴版.m4a`、`1_五线谱.pdf`）
- **路径存储**：下载后经 `_backfill_paths_to_db` 将**相对路径**写入 DB

### 5. 下载与校验结果
- download_status：`completed` **473**，`partial(3/4)` **1**（#62）
- integrity_status：`passed` **473**，`failed` **1**（#62）
- **#62 人聲版**：服务器端 404（网页有按钮但文件缺失），服务器端事实，已标记归档不重试

### 6. 关键能力
- **文件完整性校验** `verify_file_integrity()`：按扩展名检查头部（PDF=`%PDF`，M4A=`ftyp`，MP3=`ID3`/`FF`）
- **状态字段** `download_status` + `integrity_status`：下载后自动同步数据库
- **`_error` 过滤**：`print_probe_report_status()` 显示时剔除 `_error`

### 7. 交付物
- `crawler_core/probe.py`、`crawler_core/downloader.py`
- `probe_report.json`：474 首资源探测清单

---

## 五、第四阶段：项目收尾与数据校验（✅ 已完成）

### 1. 核心目标
- 对前三阶段产生的数据进行全局一致性校验。
- 针对遗留失败任务（#62）进行归档处理。
- 生成项目最终的执行统计报告。

### 2. 关键任务
- **三方对账**：DB(474) vs 目录(474) vs url_map(474)，完全一致
- **多媒体资源对账**（按版本聚合）：五线谱/简谱/鋼琴版 100%；人聲版 461/462（#62 缺失）；合唱部版 140、四部合唱版 47
- **DB 路径交叉校验**：无悬挂引用（全部相对路径命中磁盘文件）
- **失败任务归档**：#62 人聲版为服务器端 404，标记归档、不重试
- **生成报告**：`final_report.txt`（资源总数 2070，完整 2069，99%）

### 3. 交付物
- `step4_verify_and_report.py`：数据校验与报告生成脚本（含 `--rebuild-map` 从 DB 重建 url_map）
- `final_report.txt`：最终执行统计报告

---

## 六、第五阶段：PDF 转图片与图片入库（✅ 已完成）

### 1. 核心目标
- 将每首诗歌的五线谱/简谱 PDF 转换为**窄边距、高清晰 PNG** 图片。
- 双页 PDF 进行**上下拼接**为一张整图，删除分页小图。
- 以**新增字段**方式将 PNG 路径写入数据库，**不覆盖原 PDF 路径**。
- 维护每目录 `checksums.json` 的 PNG 哈希（便于 git 感知图片变更）。

### 2. 转换策略（step5_pdf2png.py）
- **格式选择**：PNG 300DPI（2481×3509px 级，比原始 PDF 小 40%；实测 SVG 方案因乐谱符号是微型位图拼合、非真矢量，体积膨胀 2.6 倍被否决）
- **窄边距**：自动检测内容包围盒（非白像素 bbox），四周保留 40px 边距
- **双页拼接**：99 首双页 PDF → `_p1.png`(上) + `_p2.png`(下) 上下拼接为同名 `.png`，宽度取较大、窄页居中，拼接后删除分页小图
- **断点续跑**：`Hymn_Downloads/step5_progress.json` 记录已完成 PDF 相对路径，`--reset-progress`+`--force` 全量重做
- **输出命名**：单页 `<基名>.png`；双页拼接后 `<基名>.png`（分页图清理）

### 3. 图片路径入库（step7_png_db.py）
- **幂等新增字段**：`staff_png_path` / `numbered_png_path`
- **回填**：从 `staff_img_path`/`numbered_img_path`(PDF 路径) 推导同名 `.png` 回填，474+474 条
- **清理分页图**：删除 `_p1.png`/`_p2.png`（仅当同名整图存在，防误删）
- **断点续跑**：`Hymn_Downloads/step7_progress.json` 记录已回填编号

### 4. 哈希清单维护（step6_update_img.py）
- 遍历每目录，将 PNG 的 `{file, sha256}` 追加/更新到 `checksums.json`
- `generate_checksums.py` 已扩展 `EXTS` 支持 `.png`（pre-commit hook 依赖，防重建时抹除 PNG 条目）
- **总计**：3017 个文件哈希（2069 原媒体 + 948 PNG）

### 5. 数据现状（第五阶段核对通过）
- 磁盘 PNG：**948 张**（每首五线谱+简谱各 1 张，含 99 首双页拼接），**0 分页残留**
- DB：`staff_png_path`/`numbered_png_path` 各 474 条，**948 条路径全部命中磁盘文件**
- checksums：474 目录，PNG 条目 1047 条与磁盘零不一致

### 6. 交付物
- `step5_pdf2png.py`：PDF→窄边距 PNG（含双页拼接）+ 断点进度
- `step6_update_img.py`：checksums.json 哈希维护
- `step7_png_db.py`：PNG 路径新增字段入库 + 断点进度

---

## 七、技术栈与环境配置
- 语言：Python 3.10+
- 虚拟环境：`/home/zjx/hymn_crawler_env`（`/home/zjx/hymn_crawler_env/bin/python3`）
- 核心库：
  - selenium：JS 渲染与页面抓取
  - beautifulsoup4：HTML 解析
  - requests：备用 HTTP 请求
  - sqlite3：本地数据库
  - concurrent.futures：并发下载/探测
  - **PIL (pillow)**：图片裁边/拼接（虚拟环境已装 12.3.0）
  - **poppler 工具**：`pdftoppm`（PDF→PNG）、`pdfinfo`、`pdftotext`（系统级）
- **代码质量工具（已装）**：ruff 0.16.2、bandit 1.9.4、mypy 2.3.0、pytest 9.1.1
- 代码检查结论：ruff 0 错误（排除 test*）、bandit 无真实漏洞（B501/B608 为刻意取舍+白名单列名）、mypy 生产代码 0 错误、pytest 0 用例（test_step* 为独立脚本）

---

## 八、开发注意事项
- 反爬策略：所有请求必须包含 User-Agent 及合理延时。
- 编码问题：统一使用 UTF-8 处理中文文件名与数据库内容。
- 幂等性：所有阶段支持重复运行，已存在的目录/数据自动跳过。
- **路径健壮性**：脚本内部 `ROOT = os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT)`。
- **时间戳**：数据库 `updated_at` 用 `datetime('now','localtime')`。
- **路径相对化**：DB 存储的路径均为相对项目根目录。
- **命令执行（全局规则）**：git 一律 `/usr/bin/git -C <绝对路径>`；长命令后台运行+轮询；禁止 `!` 字符；临时脚本三件套（写脚本→重定向输出→read 读取→用完即删）。
- **进度文件**：step2/5/7 的 `*_progress.json` 为断点临时状态，已加入 `.gitignore` 不跟踪。

---

## 九、全局任务清单 (Checklist)

### 📁 第一阶段：目录结构构建（✅ 已完成）
- [x] 编写 `test_step1.py` 测试脚本
- [x] 验证网页诗歌编号提取逻辑（含 `51_a`/`124_b` 变体）
- [x] 验证"甲、乙"同名诗歌序号不变规则
- [x] 验证本地目录创建与命名规范（`001_1頌讚獨一真神`）
- [x] 编写 `step1_create_dirs.py` 正式脚本 + `crawler_core/scanner.py`
- [x] 完整运行并生成 474 个诗歌目录
- [x] 实测 Step 1 性能：24 秒完成 24 页扫描

### 🗄️ 第二阶段：文本提取与数据库（✅ 已完成）
- [x] 编写 `test_step2.py` 测试脚本（含 10 首测试目标）
- [x] 验证详情页解析逻辑（作词、作曲、源考、歌词 Tab 切换）
- [x] 编写 `step2_extract_text.py` + `crawler_core/extractor.py`
- [x] 建立 SQLite 数据库及 `tjc_hymn` 表结构（v4→v5）
- [x] 批量抓取并入库 **474 首**诗歌文本信息
- [x] 验证数据库去重与更新机制（UPSERT + CASE 保护）
- [x] 修复时间戳时区问题（UTC → CST）
- [x] 编写 `crawler_fast.py` 统一入口
- [x] 断点续爬机制（step2_progress.json）

### 📥 第三阶段：多媒体下载与追踪（✅ 已完成）
- [x] 编写 `test_step3.py` 测试脚本
- [x] 验证音频/乐谱下载链接提取（Selenium 点击捕获 `audio#player.src`）
- [x] 验证文件下载、重命名逻辑（`{hymn_number}_{版本名}.{ext}`）
- [x] 编写 `crawler_core/` 包（7 模块）
- [x] 实现资源探测（PDF HEAD 20 并发 + 音频 4 Selenium 并行）
- [x] 修复 #410/#459 + 6 首 `_error` → 音频 **474/474**
- [x] 实现资源下载管理器（10 线程并发 + 断点续传）
- [x] 数据库迁移（v1→v4 / v2→v4 / v3→v4 / 回填）
- [x] 新增 `download_status` + `integrity_status` 字段
- [x] 文件完整性校验 `verify_file_integrity()`
- [x] 下载后回写 PDF/audio 相对路径到数据库
- [x] `audio_versions` 存储格式改为【版本名 → 相对路径字符串】

### ✅ 第四阶段：收尾与校验（✅ 已完成）
- [x] 编写 `step4_verify_and_report.py`
- [x] 三方对账 DB 474 vs 目录 474 vs url_map 474
- [x] 多媒体资源对账（按版本聚合）
- [x] 失败任务归档（#62 服务器 404，不重试）
- [x] 生成 `final_report.txt`（资源 2070，完整 2069，99%）
- [x] step4 集成进 crawler_fast.py

### 🖼️ 第五阶段：PDF 转图片与入库（✅ 已完成）
- [x] 编写 `step5_pdf2png.py`（300DPI + 窄边距 + 双页拼接 + 断点续跑）
- [x] 948 个 PDF 全部转换为 PNG（含 99 首双页拼接）
- [x] 验证拼接可行性（宽度差异平均 7.8px，最大 49px）
- [x] 编写 `step7_png_db.py`（幂等新增字段 + 回填 474+474 + 清理分页图）
- [x] 删除 198 张分页小图（安全校验：均有整图）
- [x] 编写 `step6_update_img.py` + 扩展 `generate_checksums.py` 支持 `.png`
- [x] 核对通过：DB 948 条 PNG 全命中、checksums 1047 条目零不一致
- [x] crawler_fast.py 集成（菜单 6=仅转图片、7=全流程，选项对调）

### 🔍 代码质量与清理（✅ 已完成）
- [x] 删除遗留 `main.py`（被 crawler_fast 取代，无引用）
- [x] 删除 `SESSION_SUMMARY.md`（按用户要求）
- [x] 安装 ruff/bandit/mypy/pytest 全量检查
- [x] ruff 修复 101 项（auto 52 + 人工 49），**0 错误**
- [x] bandit 19 项评估（无真实漏洞，B501/B608 为刻意取舍）
- [x] mypy 生产代码 0 错误
- [x] step5/step7 断点进度文件加 .gitignore 忽略
- [x] 字段顺序调整：download_status/integrity_status 移至末尾

---

## 十、已知问题与待优化项

### 🔧 已解决
- [x] **时间戳时区**：`CURRENT_TIMESTAMP` 返回 UTC，已改 `datetime('now','localtime')`
- [x] **详情页加载失败**：`about:blank` → `url` 跳转 + 等待渲染信号
- [x] **音频缺失**：#410/#459 + 6 首 `_error` → 重新探测修复，**474/474**
- [x] **`audio_versions` 存储格式**：改为相对路径字符串（v4）
- [x] **`save_to_db` UPSERT 覆盖路径/状态**：CASE 保护空值不覆盖
- [x] **SVG 转图方案否决**：乐谱符号是微型位图拼合，SVG 膨胀 2.6 倍，改用 PNG 300DPI
- [x] **step6 覆盖 DB 路径隐患**：精简为只维护 checksums，DB 图片路径改由 step7 新增字段管理
- [x] **pre-commit 抹除 PNG 哈希**：`generate_checksums.py` EXTS 加入 `.png`
- [x] **代码检查 142 项**：A 层 ruff 自动 52 + B 层人工 49，全部修复，ruff 0 错误

### 🔄 待优化（已知保留项）
- [ ] `#62 人聲版`：服务器端 404 事实，保持归档
- [ ] bandit B501/B608：爬虫刻意取舍（自有证书）+ 白名单列名，如需可加 `# nosec` 显式标注
- [ ] `test_step*.py` 为独立脚本非 pytest 用例；如需要可改造为 pytest 测试（当前 pytest 0 用例）
- [ ] mypy 测试文件 3 处类型警告（生产代码 0 错误，可用 `--exclude test*` 消除）

---

## 📌 当前项目文件结构（v5）
```
hymn_crawler/
├── crawler_core/              # 主代码包（7 模块）
│   ├── config.py              # 全局配置
│   ├── driver.py              # Selenium 驱动管理
│   ├── db.py                  # 数据库管理 + 迁移 + UPSERT
│   ├── scanner.py             # Step 1: 扫描列表页
│   ├── extractor.py           # Step 2: 提取详情页（断点续爬）
│   ├── probe.py               # 资源探测（PDF HEAD + 音频捕获）
│   └── downloader.py          # 资源下载 + 完整性校验 + 路径回写
├── crawler_fast.py            # 统一菜单入口（唯一主入口）
├── step1_create_dirs.py       # （遗留）Step 1 独立脚本
├── step2_extract_text.py      # （遗留）Step 2 独立脚本
├── step4_verify_and_report.py # 第四阶段：校验与报告
├── step5_pdf2png.py           # 第五阶段-1：PDF→窄边距 PNG + 双页拼接
├── step6_update_img.py        # 第五阶段-2：checksums.json 哈希维护
├── step7_png_db.py            # 第五阶段-3：PNG 路径新增字段入库
├── generate_checksums.py      # checksums.json 生成器（支持 .png）
├── tjc_hymn.db                # 数据库（v5 结构）
├── probe_report.json          # 资源探测清单（474 首，URL 对象格式）
├── final_report.txt           # 最终执行统计报告
├── test_step1.py / test_step2.py / test_step3.py   # 独立测试脚本
├── Hymn_Downloads/            # 474 首诗歌目录（含 PDF+PNG+音频+checksums.json）
├── .vscode/settings.json      # Python 解释器指向虚拟环境
├── hooks/pre-commit           # 提交前重建 checksums
└── hymn_crawler_plan.md       # 本计划文档