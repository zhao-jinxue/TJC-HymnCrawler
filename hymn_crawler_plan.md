# 真耶穌教會聖樂网爬虫开发计划

## 📑 目录
- [一、项目概述](#一项目概述)
- [二、第一阶段：目录结构构建与基础索引](#二第一阶段目录结构构建与基础索引)
- [三、第二阶段：文本信息提取与数据库架构](#三第二阶段文本信息提取与数据库架构)
- [四、第三阶段：多媒体资源下载与状态追踪](#四第三阶段多媒体资源下载与状态追踪)
- [五、第四阶段：项目收尾与数据校验](#五第四阶段项目收尾与数据校验)
- [六、技术栈与环境配置](#六技术栈与环境配置)
- [七、开发注意事项](#七开发注意事项)
- [八、全局任务清单 (Checklist)](#八全局任务清单-checklist)

> 📌 **文档更新说明**：本文件已同步到 **v4 实际状态**。第三阶段已完成，当前进入第四阶段。
> 最新状态的快速恢复指引见 `SESSION_SUMMARY.md`。

---

## 一、项目概述
本项目旨在系统化抓取真耶穌教會聖樂网（sacredmusic.tjc.org.tw）的 469 首诗歌资源。
项目采用**四阶段流水线**架构，确保数据采集的稳定性、结构化存储的规范性、多媒体资源的完整性以及最终数据的可追溯性。

> 📌 **当前实测数据**：实际扫描到 **474 首**诗歌（含 `51_a`/`51_b` 等同名变体），网站列表共 **24 页**，每页约 20 首。

---

## 二、第一阶段：目录结构构建与基础索引（✅ 已完成）

### 1. 核心目标
- 遍历网站列表页，获取全部诗歌的元数据。
- 在本地构建标准化的目录结构，为后续文件存储打下基础。

### 2. 关键规则
- **目录命名**：`诗歌编号(3位)_诗歌名称`（例如：`001_頌讚獨一真神`）。
- **序号规则**：从 `001` 开始升序，**诗歌编号从网页直接提取**（如 `51_a`、`124_b`）。
- **同名处理**：若出现"甲、乙"等同名诗歌，**诗歌编号保持不变**，仅通过名称中的"甲/乙"区分。

### 3. 性能表现
- **Step 1 扫描**：24 页，474 首，总耗时约 **24 秒**，平均每页约 **1 秒**。
- **策略**：使用 Selenium + `page_load_strategy='eager'` + 禁用图片/CSS 加载。

### 4. 交付物
- `step1_create_dirs.py`：正式脚本，包含断点续爬、异常处理。
- `crawler_fast.py`（整合）：终极极速版，Step 1 和 Step 2 共享一次浏览器会话。
- `test_step1.py`：测试脚本。

---

## 三、第二阶段：文本信息提取与数据库架构（✅ 已完成）

### 1. 核心目标
- 深入诗歌详情页，提取结构化文本信息。
- 构建本地 SQLite 数据库，实现数据的持久化与规范化管理。

### 2. 数据库设计 (`tjc_hymn`)（✅ 第三阶段已升级到 v4）
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
| `staff_img_path` | TEXT | **五线谱相对路径**（相对项目根目录） |
| `numbered_img_path` | TEXT | **简谱相对路径**（相对项目根目录） |
| `audio_versions` | TEXT | **JSON 字符串**，存储**版本名 → 本地相对路径字符串** |
| `audio_version_list` | TEXT | **JSON 字符串**，纯版本名列表（如 `["鋼琴版","人聲版"]`） |
| `download_status` | TEXT | 下载状态：`completed`/`partial(x/y)`/`failed`/`pending`/`dir_missing`/`no_files` |
| `integrity_status` | TEXT | 文件完整性：`passed`/`failed`/`unchecked` |
| `updated_at` | TIMESTAMP | 更新时间（默认本地时间 `datetime('now','localtime')`） |

> **📌 `audio_version_list` 使用场景**：
> 用户浏览诗歌详情时，先查询 `audio_version_list` 获取可用版本列表展示给用户选择；
> 确认版本后，再查 `audio_versions` 获取对应路径进行播放。无需每次遍历大 JSON 提取 keys。

> **📌 `audio_versions` 字段存储结构（v4 已变更）**：
> ```json
> {
>   "鋼琴版": "Hymn_Downloads/002_2讚美聖父/2_鋼琴版.m4a",
>   "人聲版": "Hymn_Downloads/002_2讚美聖父/2_人聲版.mp3",
>   "四部合唱版": "Hymn_Downloads/.../3_四部合唱版.m4a"
> }
> ```
> ⚠️ **v4 起存的是【版本名 → 本地相对路径字符串】，不再是线上 URL 对象。**
> 所有路径相对项目根目录（`SCRIPT_DIR`）。绝对路径 = `os.path.join(SCRIPT_DIR, relpath)`。

> **📌 历史字段说明（已弃用）**：
> 旧字段 `piano_audio_path` + `vocal_audio_path` 仅支持两种类型，已合并到 `audio_versions`。
> 首次运行 `crawler_fast.py` 时自动执行数据库迁移合并旧数据。

### 3. 数据库迁移（四段式自动执行）
迁移逻辑在 `crawler_core/db.py` 的 `init_db()` 中自动执行，支持从任意旧版本升级：

**v1 → v4（最旧版 → 最新版）**
- 检测依据：存在 `piano_audio_path` / `vocal_audio_path`，无 `audio_versions`
- 操作：备份旧表 → 创建新表（含 v4 全部字段）→ 合并旧数据 → 删除旧表 → 回填

**v2 → v4（过渡版 → 最新版）**
- 检测依据：有 `audio_versions`，缺 `audio_version_list`
- 操作：`ALTER TABLE ADD COLUMN` → 从 `audio_versions` keys 生成版本列表 → 回填 + 加状态字段

**v3 → v4**
- 检测依据：有 `audio_versions` + `audio_version_list`，缺 `download_status`/`integrity_status`
- 操作：`ALTER TABLE ADD COLUMN download_status` + `integrity_status`

**v4（已是最新版）**
- 检测依据：全部字段都存在
- 操作：从 `probe_report.json` 回填空的音频记录（幂等，不影响已有数据）

### 4. 性能表现
- **Step 2 提取**：474 首全部成功入库，平均每首约 **3 秒**（含页面渲染 + Tab 切换）。
- **策略**：`about:blank` → `url` 跳转触发 Vue SPA 路由；等待 `.author_name` 作为最可靠的渲染完成信号。

### 5. 交付物
- `step2_extract_text.py`：正式脚本，实现了验证的提取逻辑。
- `crawler_fast.py`（整合）：与 Step 1 合并的一次性会话方案。
- `test_step2.py`：测试脚本（含 10 首测试目标，含 `51_a`/`51_b` 同名诗歌）。
- `crawler_core/db.py`：数据库模块，内含四段式迁移逻辑（v1→v4 / v2→v4 / v3→v4 / 最新版回填）。

---

## 四、第三阶段：多媒体资源下载与状态追踪（✅ 已完成）

### 1. 核心目标
- 批量探测（HEAD + Selenium）全部 474 首诗歌的 PDF 乐谱与多版本音频。
- 根据探测清单并发下载所有资源到本地诗歌目录。
- 将资源路径回写至数据库（`staff_img_path` / `numbered_img_path` / `audio_versions` JSON）。

### 2. 🔍 资源探测策略（已完成）
#### PDF 探测（多线程 HEAD）
```
五线谱：HEAD https://sacredmusic.tjc.org.tw/storage/uploads/hymn/score/sheet/{编号}.pdf
简谱：  HEAD https://sacredmusic.tjc.org.tw/storage/uploads/hymn/score/num/{编号}.pdf
```
- 多线程 20 并发，每条验证状态码 200
- **结果：474/474 五线谱 + 474/474 简谱（100%）** ✅

#### 音频探测（Selenium 页面点击捕获）
- 遍历页面所有 `img.play` 按钮，过滤：`data-type` 含"讚美詩"
- 执行点击触发播放 → 读取 `audio#player.src` 获取真实音频 URL
- 排除法过滤：跳过不含"讚美詩"的按钮（YouTube 视频按钮）
- 并发：4 个 Selenium 实例并行捕获
- **结果：474/474 有音频（100%）** ✅

### 3. 音频版本分布（实测 474 首）
> 以下分布已过滤 `_error`，为**真实有效音频**数量。
| 版本 | 数量 | 占比 |
| :--- | :---: | :---: |
| 鋼琴版 | 473 | 99% |
| 人聲版 | 462 | 97% |
| 四部合唱版 | 47 | 9% |
| 合唱-1/2/3/4部版 | 各 35 | 各 7% |
| 无音频 | 0 | 0% |

> 📌 说明：原先 #410/#459 缺音频、6 首 `_error`（共 8 首），已通过**重新 Selenium 探测**全部修复，现在 **474/474 有音频**。

### 4. 下载策略
- **并发数**：10 个线程并发下载
- **断点续传**：跳过已存在的文件
- **文件命名**：`{hymn_number}_{版本名}.{ext}`（示例：`1_鋼琴版.m4a`、`1_五线谱.pdf`）
- **路径存储**：下载成功后，通过 `_backfill_paths_to_db` 将 **相对路径** 写入 `staff_img_path` / `numbered_img_path` / `audio_versions`

### 5. 下载与校验结果
- download_status：`completed` **473**，`partial(3/4)` **1**（#62）
- integrity_status：`passed` **473**，`failed` **1**（#62）
- **#62 人聲版**：服务器端该文件 404（网页有按钮但文件缺失），**服务器端事实，非代码 bug**，需在第四阶段标记归档

### 6. 关键新增能力（v4，不在原计划但已实现）
- **文件完整性校验** `verify_file_integrity()`：按扩展名检查头部（PDF=`%PDF`，M4A=`ftyp`，MP3=`ID3`/`FF`）
- **状态字段** `download_status` + `integrity_status`：下载后自动同步到数据库
- **路径回写** `_backfill_paths_to_db()`：PDF 和 audio 写入**相对路径**（跨机器可移植）
- **`_error` 过滤**：`print_probe_report_status()` 显示时剔除 `_error`，单独报告异常数

### 7. 项目文件结构（第三阶段更新）
```
hymn_crawler/
├── crawler_core/              # 主代码包（7 模块）
│   ├── config.py              # 全局配置
│   ├── driver.py              # Selenium 驱动管理
│   ├── db.py                  # 数据库管理 + 四段式迁移
│   ├── scanner.py             # Step 1: 扫描列表页
│   ├── extractor.py           # Step 2: 提取详情页
│   ├── probe.py               # 资源探测（PDF HEAD + 音频捕获）
│   └── downloader.py          # 资源下载 + 完整性校验 + 路径回写
├── crawler_fast.py            # 统一菜单入口
├── tjc_hymn.db                # 数据库（v4 结构）
├── probe_report.json          # 资源探测清单 + 各状态（474 首，音频 100%）
├── Hymn_Downloads/            # 474 首诗歌目录
├── hymn_crawler_plan.md       # 本计划文档
├── SESSION_SUMMARY.md         # 会话恢复/新会话启动文档
└── (遗留) step1/step2/test*.py
```

### 8. 交付物（第三阶段）
- `crawler_core/` 包：config, driver, db, scanner, extractor, probe, downloader
- `probe_report.json`：474 首资源探测清单（PDF 100% + 音频 100%）
- `crawler_fast.py`：统一菜单入口（启动自动打印 db/probe/url_map 状态）

---

## 五、第四阶段：项目收尾与数据校验（⏳ 待开始）

### 1. 核心目标
- 对前三阶段产生的数据、文件进行全局一致性校验。
- 针对第三阶段遗留的失败任务进行自动重试或人工干预提示。
- 生成项目最终的执行统计报告。

### 2. 关键任务
- **数据完整性校验**：对比数据库记录数(474)与本地生成的总目录数(474)，确保无遗漏。
- **多媒体资源对账**：遍历 `Hymn_Downloads` 目录，检查每首诗歌目录下是否包含完整资源文件（五线谱、简谱、钢琴音频、人声音频）+ 歌词文本（可选）。**可复用 `verify_file_integrity` 与数据库 `integrity_status`**。
- **失败任务重试**：#62 人聲版（服务器 404）处理，从数据库读取默认失败记录重试，成功后更新状态及路径。
- **增量更新机制**：对于已下载成功的资源跳过，避免重复请求。
- **生成最终报告**：输出 `final_report.txt`，包含：
  - 成功抓取总数
  - 文本完整率（有歌词占比）
  - 多媒体下载成功率（按类型分）
  - 剩余失败任务清单

### 3. 交付物
- `step4_verify_and_report.py`：数据校验与报告生成脚本。
- `final_report.txt`：项目最终执行结果统计报告。

---

## 六、技术栈与环境配置
- 语言：Python 3.10+
- 虚拟环境：hymn_crawler_env
- 核心库：
  - selenium：JS 渲染与页面抓取
  - beautifulsoup4：HTML 解析
  - requests：备用 HTTP 请求
  - sqlite3：本地数据库
  - concurrent.futures：并发下载（第三阶段）

## 七、开发注意事项
- 反爬策略：所有请求必须包含随机 User-Agent 及 time.sleep() 延时。
- 编码问题：统一使用 UTF-8 处理中文文件名与数据库内容。
- 幂等性：所有脚本必须支持重复运行，已存在的目录/数据自动跳过。
- **路径健壮性**：使用 `SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))` 确保路径独立性。
- **时间戳**：数据库 `updated_at` 使用 `datetime('now','localtime')` 而非 `CURRENT_TIMESTAMP`（时区问题）。
- **路径相对化**：数据库存储的 path 均为**相对项目根目录**，绝对路径需 `os.path.join(SCRIPT_DIR, relpath)`。

## 八、全局任务清单 (Checklist)

### 📁 第一阶段：目录结构构建（✅ 已完成）
- [x] 编写 `test_step1.py` 测试脚本
- [x] 验证网页诗歌编号提取逻辑
- [x] 验证"甲、乙"同名诗歌序号不变规则
- [x] 验证本地目录创建与命名规范
- [x] 编写 `step1_create_dirs.py` 正式脚本
- [x] 完整运行并生成 469+ 个诗歌目录
- [x] 验证断点续爬与异常处理机制
- [x] 实测 Step 1 性能：24 秒完成 24 页扫描

### 🗄️ 第二阶段：文本提取与数据库（✅ 已完成）
- [x] 编写 `test_step2.py` 测试脚本（含 10 首测试目标）
- [x] 验证详情页解析逻辑（作词、作曲、源考、歌词 Tab 切换）
- [x] 验证 `Unknown` 默认值填充逻辑
- [x] 编写 `step2_extract_text.py` 正式脚本
- [x] 建立 SQLite 数据库及 `tjc_hymn` 表结构
- [x] 批量抓取并入库 **474 首**诗歌文本信息
- [x] 验证数据库去重与更新机制（UPSERT）
- [x] 修复时间戳时区问题（UTC → CST）
- [x] 编写 `crawler_fast.py` 终极极速版（Step 1 + Step 2 一次会话）
- [x] Step 1 完成后增加用户交互选择（是否继续 Step 2）

### 📥 第三阶段：多媒体下载与追踪（✅ 已完成）
- [x] 编写 `test_step3.py` 测试脚本
- [x] 验证音频/乐谱下载链接的提取方式（Selenium 页面点击捕获 `audio#player.src`）
- [x] 验证文件下载、重命名逻辑（`{hymn_number}_{版本名}.{ext}`）
- [x] 用 `audio_version_list` 字段取代每次解析 JSON 提取版本名
- [x] 为 `tjc_hymn` 表添加 `audio_version_list` TEXT 字段
- [x] 编写 `crawler_core/` 包（7 个模块）替代旧方案
- [x] 实现资源探测（PDF 多线程 HEAD 20 并发 + 音频 4 Selenium 并行页面点击）
- [x] 恢复修复 #410/#459 + 6 首 `_error` → 音频做到 **474/474（100%）**
- [x] 实现资源下载管理器（10 线程并发 + 断点续传）
- [x] 数据库四段式迁移（v1→v4 / v2→v4 / v3→v4 / 最新版回填）
- [x] 新增 `download_status` + `integrity_status` 状态字段（v4）
- [x] 新增文件完整性校验 `verify_file_integrity()`
- [x] 下载后回写 PDF/audio **相对路径** 到数据库（`_backfill_paths_to_db`）
- [x] `audio_versions` 存储格式改为【版本名 → 相对路径字符串】
- [x] `print_probe_report_status()` 过滤 `_error` 版本

### ✅ 第四阶段：收尾与校验（⏳ 待开始）
- [ ] 编写 `step4_verify_and_report.py` 校验脚本
- [ ] 执行数据完整性校验（目录数 vs 数据库记录数，474 vs 474）
- [ ] 执行多媒体资源对账（五线谱/简谱/钢琴/人声资源完整性）
- [ ] 执行失败任务重试机制（#62 人聲版 404 标记或重试）
- [ ] 增量更新：跳过已下载成功的资源
- [ ] 生成 `final_report.txt` 最终统计报告
- [ ] 项目验收与归档

---

## 九、已知问题与待优化项

### 🔧 已解决
- [x] **时间戳时区**：`CURRENT_TIMESTAMP` 返回 UTC，已改为 `datetime('now','localtime')`
- [x] **详情页加载失败**：改用 `about:blank` → `url` 跳转 + 等待 `.author_name`
- [x] **Step 1 无耗时统计**：已添加每页和总体耗时
- [x] **音频缺失**：#410/#459 + 6 首 `_error` → 重新探测修复，达到 **474/474 有音频**
- [x] **`audio_versions` 存储格式**：改为相对路径字符串（v4），不再存 URL 对象

### 🔄 待优化
- [ ] **目录命名**：当前含全角空格和编号前缀（如 `001_1　頌讚獨一真神`），建议改为 `001_頌讚獨一真神`，但已有 474 个目录，改造成本高，可留到第四阶段统一清理
- [ ] **Step 2 速度**：平均每首 ~3 秒（含 about:blank 中转 + 0.5s sleep），后续可尝试去掉 sleep，用更精确的等待条件替代
- [ ] **内存管理**：474 首全部存在内存列表中再逐首入库，可改为分批处理（每 50 首 flush 一次）
- [ ] **断点续爬**：Step 2 异常中断后无法从中断处恢复（没有记录已处理的编号）
