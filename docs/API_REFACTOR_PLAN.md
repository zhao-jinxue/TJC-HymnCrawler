# 官网 JSON API 重构方案（设计文档 · 待评审）

> 版本：v1.2（2026-09-12 修订）　状态：**决策已定稿、数据先行项已落地，P0 就绪**
> 关联会话日志：`docs/sessions/2026-09-12_21-47-42.md`（任务 2、任务 3、任务 4）
> 结论一句话：**可行，收益 ≈10×；API 为主、Selenium 完整保留为保底（`crawler_core/selenium_legacy/`）；P0 → P1 → P2 分三阶段推进**

---

## 0. TL;DR

| 维度 | 现状（Selenium） | 改后（官网 API） |
| --- | --- | --- |
| Step 1 列表扫描 | 48 页 × 2.76 s ≈ **2.2 min** | 48 请求 ≈ **5 s**（6 线程） |
| Step 2 详情（474 首） | 实测 2.27 s/首 ≈ **17.9 min** | 实测 0.21 s/首（10 线程）≈ **100 s** |
| 资源探测 · PDF | 948 次 HEAD ≈ 0.5 min | URL 由 API 直接给出（HEAD 校验保留） |
| 资源探测 · 音频 | 474 页逐个点击播放 ≈ **8–10 min** | **0 s**（随数据一起返回） |
| **合计（元数据 + 资源探测）** | **≈ 28–30 min** | **≈ 2–3 min** |
| 数据完整性 | 音频 **1119 条可用**（清理前本地 1257 条，含 136 重复） | 音频 **1119 条可用 / 474 首**（另 10 条源站不可用；已清除 136 个重复文件，本地 1121 条） |
| 增量更新 | 只能全量重扫 | 依 `updated_at` 真增量 |
| 运行依赖 | Chrome + chromedriver 版本匹配 | 纯 requests（Selenium 移入 `selenium_legacy/` 作保底） |
| 失败处理 | 无重试、坏链永久 `partial`、原因不落盘 | 四层容错 + URL 预检 + `_unavailable` 分类记录（§5.9） |

关键前提（已实测）：**列表接口 `/api/hymn` 每条记录 = 详情接口 `/api/hymn/{no}` 的完整记录**（仅详情多 `prev_no`/`next_no`），因此**全量 474 首只需 48 个请求、2.2 MB**。

> 📌 版本变更：
> - **v1.1（校准）**：① 音频口径——API 侧 1130 条中 **9 条 `file_url=null` 空记录 + 1 条 404（#62 人聲版）**，另有 **136 个本地重复文件**；② 新增 §5.9 失败与异常处理设计、§4.4 双引擎保底布局。
> - **v1.2（决策定稿 + 数据先行，2026-09-12 用户拍板 10 项，见 §10）**：
>   - **已执行的数据操作**：删除 136 个重复文件（**-134.7 MB**，逐对 md5 复核）；#201 人聲版 `.mp4 → .m4a` 归一化；#349 目录迁移为 `354_349奇妙的耶穌`（目录 + `url_map.txt` + `probe_report.json` + `step5_progress.json` + DB 6 字段全链同步）。
>   - **已定稿的设计选择**：保底包命名 **`crawler_core/selenium_legacy/`** + 独立整链入口 **`crawler_selenium.py`**（必备）；DB v7 **只加一列 `api_raw`**；`hymn_category` **用 API 重建整表**；`api_cache/` 落盘并**纳入 git 跟踪**（约 2.2 MB）；不可用资源**不计入期望集合**（#62 → `completed` + `_unavailable`）；`.mp4 → .m4a` 归一化。

---

## 1. 背景与目标

### 1.1 现状痛点
1. **慢**：Step 2 单线程 2.27 s/首 × 474 ≈ 18 min；资源探测又要 474 次页面点击播放（音频 URL 只能靠 `audio#player` 的 src 拿到）。
2. **脆**：依赖 Chrome / chromedriver 版本匹配；页面为**客户端渲染**（原始 HTML 里 `div.music` / `.lyrics_box` / `music_title` 均无数据），DOM 结构一变就全挂；音频探测靠 `time.sleep(1.5)` 等异步播放器加载，偶发漏抓（本次音频少 11 首即此原因）。
3. **信息有损**：仅能拿到页面展示的字段；官网 API 里现成的 `category` / `tags` / `composers` / `lyricists` / `youtube_urls` / `prev_no` / `updated_at` 全部拿不到（或拿不全会）。
4. **无法增量**：没有 `updated_at`，只能全量重扫。
5. **失败处理粗糙**（本轮二次实测发现）：
   - 资源 URL **无预检**：坏链要等下载阶段才暴露，且被永久计入"期望文件"分母 → 例如 #62 人聲版（服务端 404）使该首永远是 `partial(3/4)` + `integrity_status=failed`；
   - 下载失败**只打印不落盘**：`probe_report.json` 里没有失败原因/HTTP 状态码，下次运行分不清"没试过"与"试过且 404"；
   - **无重试**：实测 2078 个资源 URL 中有 33 个（≈1.6%）瞬时失败（超时/SSL 抖动），现状会直接判失败；
   - 归档说明**硬编码**在 `verify.py:301`（`if h == "62"`），新增坏链不会被记录。

### 1.2 目标
- **性能**：元数据 + 资源 URL 采集从 ~30 min 降到 ~2–3 min（≈10×）。
- **稳定性**：默认路径不依赖浏览器；API 异常时自动降级 DOM。
- **数据质量**：以 API 为权威源，补齐音频、修正元数据、补全分类/标签/YouTube。
- **可增量**：支持「只同步官网变动过的诗歌」。

### 1.3 非目标（本次不做）
- 不改下载器（`downloader.py`）、转图片（`images.py`）、哈希（`checksums.py`）、校验（`verify.py`）的下游逻辑与产物格式。
- 不做前端/展示层。
- 不改变现存目录名、文件名、`url_map.txt` 格式（保证历史文件零迁移）。

---

## 2. 现状架构与 Selenium 使用点

| 模块 | 职责 | 是否用 Selenium |
| --- | --- | --- |
| `crawler_core/driver.py` | 唯一浏览器入口 `init_driver()` | ✅ 定义 |
| `crawler_core/scanner.py` | Step 1：列表页翻页 + 建目录 + `url_map.txt` | ✅ |
| `crawler_core/extractor.py` | Step 2：详情页文本（标题/词曲/源考/歌词）→ DB | ✅（歌词已 API 优先，DOM 兜底） |
| `crawler_core/probe.py` | 资源探测：PDF（requests HEAD）+ 音频（Selenium 点击） | ✅（音频部分） |
| `crawler_core/downloader.py` | 按 `probe_report.json` 并发下载 + 完整性校验 | ❌ |
| `crawler_core/images.py` / `checksums.py` / `verify.py` | PDF→PNG、哈希清单、对账报告 | ❌ |
| `crawler_core/db.py` | SQLite（v6：含 `chorus`） | ❌ |
| `crawler_core/lyrics_api.py` | 歌词 API 刷新（本次新增） | ❌（已用 API） |

数据流（现状）：
```
scanner(Selenium 列表页) → url_map.txt + 目录
        ↓
extractor(Selenium 详情页 + lyrics_api 歌词) → tjc_hymn
        ↓
probe(Selenium 点击音频 + requests HEAD PDF) → probe_report.json
        ↓
downloader → images → checksums → verify（纯 requests / poppler）
```


---

## 3. 取证结果（2026-09-12 实测，脚本见附录 A）

### 3.1 API 全貌

从前端 bundle `entry.67a13066.js` 提取到的端点：

| 端点 | 说明 | 实测 |
| --- | --- | --- |
| `GET /api/hymn?page=N` | **列表**，每页固定 10 条（`per_page` 参数被服务端忽略，传 500 仍返回 10） | 48 页 / 474 首，`total=474` |
| `GET /api/hymn/{no}` | 详情（含 `prev_no` / `next_no`） | 200 |
| `GET /api/hymn/category/{id}` | 分类详情 | 200 |
| `GET /api/hymn/search`、`/api/search` | 搜索 | 200 |
| `GET /api/tag` | 标签（247 条） | 200 |
| `/api/hymn/locals`、`/api/random-songs`、`/api/todays-song` | 其它 | 未用 |

> 注意：这些是站点**内部接口**，非公开承诺；设计上必须做「字段完整性自检 + DOM 降级」。

单条记录字段（474 首全量统计）：

| 字段 | 非空覆盖 | 内容 |
| --- | --- | --- |
| `lyrics[{text}]` | 474/474 | 正歌（每元素一节，`\r\n` 分行） |
| `lyrics_chorus` | **270/474** | 副歌（与本次修复结论完全一致） |
| `sheet_score_pdf_url` | 474/474 | 五线谱 PDF 直链（`/storage/uploads/hymn/score/sheet/{no}.pdf`） |
| `num_score_pdf_url` | 474/474 | 简谱 PDF 直链 |
| `audio_files[{file_url, audio_category.name}]` | 474/474 首、**1130 条** | 音频直链 + 版本分类 |
| `history` | 473/474 | 诗歌源考（HTML，含 `<p>`/`<br>`/`&ldquo;`） |
| `lyricists` / `composers` | 442 / 473 | 多值数组（含 name / id / image_url） |
| `category` | 474/474 | 分类对象（45 类，如 `讚美耶穌`） |
| `tags` | 1/474 | 标签基本未维护 |
| `youtube_urls` | 471/474 | `[{label,url}]` |
| `sheet_score_images` | **0** | 全为 `[]`（无图像版乐谱） |
| `num_score_xml_url` | **0** | 全为 `null`（无 MusicXML） |
| `updated_at` | 474/474 | 增量同步依据 |

### 3.2 与现有产物的对账（474 首逐首）

| 项目 | API | 现有（probe_report / DB） | 差异 |
| --- | --- | --- | --- |
| 编号集合 | 474 | 474 | **完全一致**（无多无少） |
| 五线谱 PDF | 474 | 474 | **零差异**；直链实测 `200 application/pdf`（如 12 号 81299 B） |
| 简谱 PDF | 474 | 474 | **零差异**（如 12 号 774720 B） |
| 音频（API 列出） | 474 首 / 1130 条 | 清理前 474 首 / **1257 条**（本地文件） | 见下方「音频口径二次校准」 |
| 音频（**实测可下载**） | 474 首 / **1119 条** | 清理后本地 **1121 条** | **缺失 0**；本地仅多 2 条（#349 已下架，保留）；API 侧 10 条不可用 |
| 歌词 / 副歌 | 474 / 270 | 474 / 270 | **逐首 0 不一致**（本次修复后） |
| 元数据 | — | — | 17 首 `composer` DB 为 `Unknown` 而 API 有名字；349 标题/词作者 API 为新值；`source_info` 与 `history` 同源（差异仅 HTML 实体与换行） |

音频版本名（API 分类名 + `版` = 现有文件名，**规则已实测吻合**）：

```
API : 鋼琴 / 人聲 / 四部合唱 / 合唱-1部 … 四部合唱-4部
文件: {no}_鋼琴版.m4a / {no}_人聲版.mp3 / {no}_四部合唱版.m4a / {no}_合唱-1部版.* …
```

（分布：鋼琴 475、人聲 469、四部合唱 46、四部合唱-1..4 部各 34、合唱-1..4 部各 1）

> ⚠️ **音频口径二次校准（2026-09-12 补测，推翻初轮"API 多 11 首 12 条、可补 11 首"之说）**
>
> 对 API 全部 **1130 条**音频 URL 逐条实测（HEAD + Range，含复测）：
> - **可下载 1119 条 / 474 首**；
> - **不可用 10 条**：
>   - **`file_url` 与 `file` 同时为 `null`（空记录）9 条**：#178、#249、#255、#268、#274_b、#308、#386、#387、#389 各 1 条；
>   - **HTTP 404 1 条**：#62 人聲版（`…/audio/0dcc19451da297d5a15105d3e7eea85d.m4a`）——**与 Selenium 抓到的 URL 逐字符相同**，属服务端文件缺失，换 API 不会自动修好；
> - 因此初轮统计的"API 多出 11 首"多数是上述空/坏记录 → 真正可补的仅 **1 条：#201 人聲版**（URL 后缀 `.mp4`，实测 200 `video/mp4`、2 489 768 B、头部 `ftyp` 即 MP4/M4A 容器）；
> - **#201 终验结论（2026-09-12）**：该文件其实**早已下载**（本地 `201_人聲版.mp4`，2 489 768 B），与远端**md5 完全一致**（`c6b9c8fce636487f7107d3bae315a462`）→ **无需补下载**；此前"可补 1 条"是统计脚本的 **`.mp4` 盲点**（`RESOURCE_EXTS` 未含 `.mp4`，本地已有文件对统计"隐身"）造成的误判。已按 §10 ⑩ 归一化为 **`201_人聲版.m4a`**（文件 + `checksums.json` + `probe_report.ext` + DB 路径四处同步）；
> - 反向差异：**清理前本地比 API 多 138 条** = 34 首 × 4 条 `合唱-N部版`（与 `四部合唱-N部版` **逐字节相同**，历史重复副本，134.7 MB）+ #349 的 `人聲版`/`四部合唱版`（官网已下架，保留不删）；
> - **终版口径（已落地）**：音频**缺失 0**，API 可用 1119 条全部在本地；已删除 136 个重复文件；仅余 2 条已下架文件（#349）留档。详见 §3.6、§3.7 与 §5.9。

> ℹ️ **`seq` 与 `hymn_number` 必须分开对待（实测）**
>
> `url_map.txt` 第一列是 **列表位置（1…474）**，第三列 URL 末尾是**诗歌编号**（`no`）；两者有 **423/474 条不相等**：
> - 因存在 5 对「甲/乙」互见编号（`51_a/51_b`、`124_a/124_b`、`132_a/132_b`、`274_a/274_b`、`296_a/296_b`）→ 编号总数 469、列表条目 474；
> - 例：`063|063_62願主偕行|…/hymn/62`、`201|201_198靠主領回天家|…/hymn/198`、`354|354_349救主正在等候|…/hymn/349`。
>
> → **目录名用 `seq`、资源文件名用 `hymn_number`**，API 版实现不得混用（`no` 是字符串，可能是 `"51_b"`）。

### 3.3 列表 = 详情（设计关键）

对 5 首逐字段比对列表项与详情记录：**同名字段值不同 = 0 个，仅详情独有 `prev_no`/`next_no`**。
→ 结论：**Step 1 与 Step 2 可合并为 48 个请求**（全量 2.2 MB，单首均 4.7 KB）。

### 3.4 速度实测（同机）

| 阶段 | Selenium | API |
| --- | --- | --- |
| 列表 1 页 | 2.76 s（且 `page_source` 中 `div.music` 命中 0 条，须等 JS 渲染） | 0.61 s（10 条） |
| 详情 1 首 | 2.27 s/首（6 首实测 13.6 s，含 `_parse_one` 全部 DOM 等待） | 0.212 s/首（10 线程 6 首 1.27 s） |
| 并发容忍 | 单线程 | 16 线程 × 64 请求 = 11 req/s，**无 429 / 无封禁** |

### 3.5 兼容性验证（决定"零迁移"是否成立）

| 检查项 | 结果 |
| --- | --- |
| API 返回顺序 == 现有 `seq` 序号 | **474/474 一致** |
| 目录名 == `f"{seq:03d}_{sanitize(no+name)}"`（sanitize 沿用 `Scanner._create_dir`：仅保留字母数字与空格/下划线/连字符） | **474/474**（原 473/474；唯一例外 **#349** 已按 §10 ③ 迁移为 `354_349奇妙的耶穌`，迁移后规则全覆盖） |
| 文件名规则（`{no}_五线谱.pdf` / `{no}_简谱.pdf` / `{no}_{版本}版.{ext}`） | 抽查 4 首 **全部命中** |

→ 兼容策略：**目录名以现有 `url_map.txt` 为准，API 只做「校验 + 新增」**（标题变更**不自动重命名**，只在报告中标红；#349 已按 §10 ③ 一次性人工迁移并同步 `url_map`/`probe_report`/DB），新增诗歌按同一 sanitize 规则生成。

---

### 3.6 资源 URL 可下载性全量实测（2078 个 URL，回答"有链接但下载不了"）

对 API 给出的**全部**资源 URL 做可达性实测（948 个 PDF + 1130 条音频，16 线程 HEAD，失败者用 GET `Range` 复测至多 3 次）：

| 轮次 | PDF | 音频 | 说明 |
| --- | --- | --- | --- |
| 首轮 200 | 942 / 948 | 1102 / 1130 | 非 200 共 **34** 条 |
| 复测后 | 948 / 948 | 1120 / 1130 | 33 条**恢复 200**（瞬时抖动） |
| 确认不可用 | **0** | **10**（9 空记录 + 1 个 404） | 见 §3.2 校准 |

瞬时失败构成：`ReadTimeout` 13、`SSLError` 5、`MissingSchema` 9（= 空 URL 记录，非网络问题）。

**三条可直接落地的结论**：
1. **站点无严格限流**：16 线程连续 2078 请求未见 429 → 并发 8 是安全值。
2. **瞬时失败率 ≈ 1.6%（33/2078）**：**API 版必须带重试**，否则会凭空丢 PDF/音频（现状 downloader 无重试，正属此类隐患）。
3. **"有链接" ≠ "能下载"**：必须把「网络抖动（可重试）」与「资源真缺失（4xx/空 URL，不可重试）」**分类记录**，否则期望清单与 `download_status` 会永久失真（#62 即活例）。

### 3.7 历史重复文件（已对账确认并清理，134.7 MB）

| 项目 | 结果 |
| --- | --- |
| 现象 | 34 首诗歌目录内同时存在 `{no}_合唱-1..4部版.m4a` 与 `{no}_四部合唱-1..4部版.m4a` |
| 实测（全文件 md5） | **全部 136 对逐字节相同**（复测范围由抽样扩至 34 首 × 4 部，脚本 `/tmp/check_dup2.py`）→ **同一文件的两份副本** |
| 规模 | **136 个重复文件 / 134.7 MB**（另 #6 的 `合唱-1..4部` 是 API 真实分类，不计入重复） |
| 成因 | 站点分类名历史上由「合唱-N部」更名为「四部合唱-N部」；两轮 Selenium 采集各存一份（DOM 标签驱动命名，无权威分类校验） |
| API 现状 | 只列 `四部合唱-N部`（34 首）与 `合唱-N部`（仅 #6「頌主造化大功」一首）→ **API 版天然不产生重复** |
| 引用核对 | 136 个文件**均未被 `probe_report.json` / DB `audio_versions` 引用**（仅 #6 的 4 个被引用）→ 属历史遗留孤儿文件，删除不影响任何期望集合 |
| 处理 | ✅ **已删除（2026-09-12）**：删除前**逐对复核 md5 与孪生文件完全相同**，删除 136 个文件 + 同步 34 个目录的 `checksums.json`，释放 **134.7 MB**；#6 的 4 个（API 真实分类）保留。清理后 `verify.py` 三方对账仍 ✅ 完全一致、`pytest` 23 项全绿 |

---

## 4. 目标架构

### 4.1 数据流（改后）

```
                        ┌──────────────────────────────┐
  api_client.py  ─────▶ │ 48 个请求 = 474 首全量记录    │
 （重试/退避/并发/缓存）│ （歌词/副歌/PDF/音频/元数据） │
                        └──────────────┬───────────────┘
                                       │
        ┌──────────────────────────────┼───────────────────────────────┐
        ▼                              ▼                               ▼
  scanner（列表→目录）        extractor（字段→DB）              probe（资源清单）
  url_map.txt 格式不变         tjc_hymn（v7 新列）            probe_report.json 结构不变
        │                              │                               │
        └──────────────┬───────────────┴───────────────┬───────────────┘
                       ▼                               ▼
              downloader.py  ─────────────────▶  images.py / checksums.py / verify.py
              （零改动）                            （零改动）

  兜底路径（仅 API 字段缺失/异常时触发，按开关启用）：driver.py(Selenium) + DOM 解析
```

### 4.2 模块改动总表

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `crawler_core/api_client.py` | **新增** | 统一 API 客户端：分页列表、详情、分类；重试退避、并发上限、可选磁盘缓存、字段完整性自检 |
| `crawler_core/config.py` | 修改 | `API_LIST_URL` / `API_CATEGORY_URL` / `API_MAX_WORKERS` / `API_TIMEOUT` / `API_CACHE_DIR` / `USE_SELENIUM_FALLBACK` |
| `crawler_core/scanner.py` | 修改 | 默认走 API 列表；保留 `_create_dir`/`_save_map` 逻辑与 sanitize 规则不变；Selenium 版本保留为 `scan_legacy()` |
| `crawler_core/extractor.py` | 修改 | `_parse_one` 改为「API 直出全部字段」，DOM 解析降级为 fallback；`group_lyrics_boxes` 保留（兜底用） |
| `crawler_core/probe.py` | 修改 | `_do_probe` 改为「API 构造 PDF/音频 URL + HEAD 校验」；音频点击逻辑保留为 `_probe_audios_legacy()` |
| `crawler_core/driver.py` | 修改 | 收敛为**转发层**（`from .selenium_legacy.driver import init_driver`），保持旧 import 可用；Selenium 依赖改为函数内延迟导入 |
| `crawler_core/naming.py` | **新增** | 双引擎**唯一命名真源**：`sanitize()` / `to_dirname(seq, no, name)` / `pdf_name(no, kind)` / `audio_name(no, ver, ext)` / `API 分类名 + "版"` 映射 |
| `crawler_core/selenium_legacy/` | **新增** | Selenium 保底实现整体迁入（`driver.py` / `scanner_selenium.py` / `extractor_dom.py` / `probe_audio.py` + README），默认不参与主流程 |
| `crawler_core/db.py` | 修改 | v7 迁移（幂等 `ADD COLUMN`）：**只新增一列 `api_raw`**（API 原始记录 JSON），另按 §5.6 用 API 重建 `hymn_category` 表 |
| `crawler_selenium.py` | **新增** | Selenium 保底**独立整链入口**（等价重构前行为，菜单/步骤/DOM 引擎），`--engine selenium` 亦可触发 |
| `Hymn_Downloads/api_cache/` | **新增** | 48 页 API 响应落盘缓存（约 2.2 MB，**纳入 git 跟踪**；文件名 `page_01.json`…，供离线对账与复现） |
| `crawler_core/lyrics_api.py` | 保留 | 与 `api_client` 共享 `fetch_hymn_lyrics`（转为薄封装，避免重复实现） |
| `crawler_fast.py` | 修改 | 菜单：Step1/Step2/探测切到 API；新增「10 极速全量同步（纯 API）」；「8 补全」支持 API 重试 |
| `README.md` / `docs/SESSION_SUMMARY.md` | 修改 | 用法与架构说明同步 |
| `test/test_api_client.py` | **新增** | 离线 fixture 单测（分页拼接/字段映射/版本名/重试退避/缓存） |

### 4.3 `api_client.py` 接口草案

```python
# 常量（config.py 统一提供）
TIMEOUT = 30
MAX_WORKERS = 8
RETRIES = 3

def fetch_page(page: int) -> dict            # 原始分页响应（含 data/current_page/last_page/total）
def iter_hymns(page_limit=None, workers=6)   # 生成器：并发拉全部页 → 逐首 yield dict
def fetch_all(workers=6) -> list[dict]       # 一次拿全量（474 首 ≈ 5–10 s，2.2 MB）
def fetch_hymn(no: str) -> dict | None       # 详情（含 prev_no/next_no）
def fetch_category(cid: int) -> dict
def fetch_with_retry(url, *, retries=3, backoff=1.5)  # 429/5xx/超时 退避重试
def validate_record(rec) -> list[str]        # 字段完整性自检（缺失的必填字段列表）
def cache_get(key) / cache_put(key, obj)     # 可选：Hymn_Downloads/api_cache/*.json

# 领域映射（纯函数，便于单测）
def to_song(rec) -> dict          # → scanner 的 song 结构 {seq_num, hymn_number, title, url}
def to_dirname(seq, rec) -> str   # → f"{seq:03d}_{sanitize(no+name)}"
def to_audio_versions(rec) -> dict  # {"鋼琴版": {url, filename, ext}, "人聲版": {...}}
def to_source_info(rec) -> str    # history → 纯文本（html.unescape + <br>/<p> → \n）
def to_lyrics(rec) -> (list[str], str)  # 正歌 verses / 副歌 chorus
def to_metadata(rec) -> dict      # title/lyricist/composer/category/tags/youtube/updated_at
```

设计要点：
- **一次 48 请求拿全量**（默认路径），详情接口仅用于**单首补抓/校验**（比全量更省，且能拿 `prev_no/next_no`）。
- 所有 `requests.get(..., verify=False)` 均带 `# nosec B501`（与 `downloader.py` 一致）。
- 失败重试：`429/5xx/超时` 退避 `1.5^n` 秒，最多 3 次；整体失败才触发 DOM 降级。
- **URL 归一化**：`file_url` 为 `null`/空 → 判定 `unavailable`；后缀 `.mp4` → 落盘按 `.m4a` 处理（同一容器）。

### 4.4 双引擎与 Selenium 保底目录（按用户 2026-09-12 建议）

**目标：新方法（API）为主，Selenium 完整保留为可运行保底，且主流程零浏览器依赖。**

```
crawler_core/
├── api_client.py          ★ 新增：API 客户端（重试/退避/并发/缓存/自检）
├── naming.py              ★ 新增：目录名·文件名·版本名规则（双引擎共用真源）
├── scanner.py               改造：scan_api()（默认） / scan_legacy()（委托 selenium_legacy）
├── extractor.py             改造：API 主路径 + _parse_one_dom() 降级
├── probe.py                 改造：API 清单 + URL 预检 + _probe_audios_legacy() 降级
├── driver.py                改造：转发层（from .selenium_legacy.driver import init_driver）
└── selenium_legacy/       ★ 新增：Selenium 保底包（默认不参与主流程）
    ├── __init__.py
    ├── driver.py              WebDriver 工厂（懒导入 selenium）
    ├── scanner_selenium.py    原「列表页翻页 + DOM 解析」
    ├── extractor_dom.py       原 _parse_one DOM 解析 + group_lyrics_boxes
    ├── probe_audio.py         原「音频点击捕获」
    └── README.md              用途/启停方法/何时该用
crawler_selenium.py        ★ 新增（必备）：纯 Selenium 整链独立入口，等价重构前行为
requirements-selenium.txt  ★ 新增（必备）：selenium 移出主依赖，按需安装
```

| 维度 | 设计 |
| --- | --- |
| 命名（2026-09-12 拍板） | 保底包目录名 **`selenium_legacy`**（语义明确：这是 Selenium 的旧引擎实现）；整链入口 **`crawler_selenium.py` 为必备项**（不是可选） |
| 引擎开关 | `--engine api \| selenium \| auto`（+ `config.CRAWL_ENGINE`），默认 `api`；`auto` = API 优先，**逐首**校验失败才降级该首 |
| 依赖策略 | 主依赖不含 `selenium`；`selenium_legacy/*` 内部才 `import selenium` → 未装也能跑 API 全流程，装了即可用保底 |
| 兼容策略 | `driver.py` 顶层保留转发 → 旧代码 `from crawler_core.driver import init_driver` 不破；`scanner.scan_legacy()` / `probe._probe_audios_legacy()` 方法名保留 |
| 命名一致性 | 两个引擎**都必须**调用 `naming.py` → 杜绝「同一内容存两份不同版本名」这类规则漂移（历史上已产生 136 个 `合唱-N部版` / `四部合唱-N部版` 重复文件，已于 §3.7 清理） |
| 为什么用子包而非顶层副本 | 相对导入（`..config`）改动最小、`crawler_core` 仍是唯一包、测试与入口无需双份维护；顶层 `crawler_selenium.py` 作为**独立整链入口**满足「保底可整体替换」诉求 |
| 测试 | `selenium_legacy/*` 不进默认 `pytest`（无浏览器 CI 也能全绿），以 `pytest -m selenium` 单独可选运行 |

**保底触发条件**（`auto` 模式）：API 请求整体失败 / `validate_record` 报必填字段缺失 / 该首 API 记录异常（`no` 不匹配、`lyrics` 为空）→ 仅该首走 DOM。

---

## 5. 详细设计

### 5.1 字段映射表（API → DB / 产物）

| API 字段 | 目标 | 规则 |
| --- | --- | --- |
| `no` | `tjc_hymn.hymn_number` | 原样（如 `"51_b"`） |
| `name` | `tjc_hymn.title` | 原样（详情页标题，不含编号前缀） |
| `lyrics[].text` | `verse_1..verse_10` + `verse_count` | 逐节去首尾空白；CRLF → LF；超过 10 节截断并告警 |
| `lyrics_chorus` | `chorus`（v6 已有） | 同上；空则存 `""` |
| `lyricists[].name` | `lyricist` | 多值用 `、` 连接；空则 `Unknown` |
| `composers[].name` | `composer` | 同上 |
| `history`（HTML） | `source_info`（+ 原 HTML 保留在 `api_raw`） | `html.unescape` → `<br>`/`</p><p>` → `\n` → 去标签 → `strip()` |
| `sheet_score_pdf_url` | `probe_report.staff_pdf`（DB 侧留在 `api_raw`） | 原样 |
| `num_score_pdf_url` | `probe_report.numbered_pdf`（DB 侧留在 `api_raw`） | 原样 |
| `audio_files[].file_url` | `probe_report.audio_versions[版本+版]` | 版本名 = `audio_category.name + "版"`；`filename` = URL 末段；`ext` = 后缀（`.mp4` → `m4a`，§5.9） |
| `category` / `tags` / `youtube_urls` / `updated_at` / `prev_no` / `next_no` | **`api_raw`（单列 JSON）** | 不建列；`category` 另由 API 重建 `hymn_category` 表（§5.6） |

`probe_report.json` 的结构与键名**完全不变**，因此 `downloader.py` 无需改动；DB 侧新增信息一律进 `api_raw`，现有列语义不变。

### 5.2 `scanner.py`（Step 1）

```python
class Scanner:
    def scan_api(self) -> list[dict]:        # 默认：48 请求拿全量 → song 列表 → 建目录 → 写 url_map
    def scan_legacy(self) -> list[dict]:     # 原 Selenium 翻页逻辑（保留，开关启用）
```
- `_create_dir` / `_save_map` / `_load_existing` **逻辑与 sanitize 规则完全保持不变**（保证历史目录不重建），并统一改调用 `naming.py`（双引擎共用）。
- 目录名 = `f"{seq:03d}_{sanitize(no + name)}"`，其中 **`seq` = 列表位置（1…474）、`no` = 该首编号（可能含 `_a/_b`）**；实测两者 423/474 不相等（见 §3.2 说明），实现中不得混用。
- 新增校验：若 `f"{seq}_{sanitize(no+name)}"` 与 `url_map.txt` 既有目录名不符，**沿用既有目录名并打印提示**，仅登记差异不重命名（#349 已于 2026-09-12 人工迁移为 `354_349奇妙的耶穌`，迁移后 474/474 全部符合规则）。
- 新增 `--check` 模式：只比对「API 列表 vs url_map vs 本地目录」三方一致性，不落盘。

### 5.3 `extractor.py`（Step 2）

```python
def _parse_one(self, driver, song):
    rec = api_client.fetch_hymn(song["hymn_number"])
    if rec and not api_client.validate_record(rec):
        return api_client.to_db_record(rec)        # 主路径：API 直出全部字段
    if not config.USE_SELENIUM_FALLBACK:
        raise ApiUnavailableError(...)             # 或返回空并计入 failed
    return self._parse_one_dom(driver, song)       # 降级：原 DOM 解析（含 group_lyrics_boxes）
```
- 原 `_parse_one` 主体重命名为 `_parse_one_dom`，仅做最小改动（歌词段仍调用 `group_lyrics_boxes`）。
- API 路径下 `driver` 完全不使用、也不创建浏览器 → `Extractor.extract_all(driver=None)` 走 `ThreadPoolExecutor`（并发 8）纯 API 抓取。
- 断点续爬沿用 `step2_progress.json`。

### 5.4 `probe.py`（资源探测）

```python
def _do_probe():
    recs = {r["no"]: r for r in api_client.fetch_all()}     # 1 次拿全量
    for no, rec in recs.items():
        staff, num = rec["sheet_score_pdf_url"], rec["num_score_pdf_url"]
        audio = api_client.to_audio_versions(rec)
        _verify_urls([staff, num])                          # requests HEAD（并发 10，可关）
        manifest.append({..., "staff_pdf": staff, "numbered_pdf": num,
                         "audio_versions": audio, ...})     # 结构不变
```
- 音频不再需要点击播放；PDF 保留 **HEAD 可达性校验**。
- **新增 URL 预检（关键）**：对每个 PDF/音频 URL 做 `HEAD`（失败再 `GET Range`，各重试 2 次），结果写入
  `audio_versions[ver]["_http_status"]` / `["_error"]` 与 `_pdf_status`；**不可用者不计入期望文件集合**，
  从而修正 `download_status`/`integrity_status` 语义（#62 从"永久 partial"变为"completed + 1 条 unavailable"）。
- `.mp4` 后缀归一化为 `.m4a`（见 §5.9），避免下游 `RESOURCE_EXTS={.pdf,.m4a,.mp3}` 漏统计。
- 原 `_capture_song_audio` 改名 `_capture_song_audio_legacy`，仅在 API 无 `audio_files` 时用于单首兜底。
- `run_probe_missing()`（增量补探音频）语义改为「API 与本地清单的差集」，接口保留。

### 5.5 `driver.py` 与依赖

```python
def init_driver():
    from selenium import webdriver          # 延迟导入：无 Chrome 环境也能跑纯 API 流程
    ...
```
- 主依赖**不含** `selenium`：`crawler_core/selenium_legacy/*` 内部才 `import selenium` → 无 Chrome 环境也能跑纯 API 全流程；需要保底时 `pip install -r requirements-selenium.txt` 即可（可用 `crawler_selenium.py` 独立入口整链运行）。
- `driver.py` 保留为转发层，旧 import 路径不破；`selenium_legacy/README.md` 说明启停方法与使用场景。

### 5.6 DB v7 迁移（幂等，向后兼容）· 已定稿：单列 `api_raw`

**决策（2026-09-12 拍板）**：不逐字段建列，**只新增一列 `api_raw`**，存放该首的 API 原始记录 JSON。

```sql
ALTER TABLE tjc_hymn ADD COLUMN api_raw TEXT DEFAULT '';   -- 整条 API 记录（名称/分类/标签/历史/YouTube/updated_at/prev_no/next_no…）
```
- 沿用 v6 的 `ensure_chorus_field` 范式：新增 `ensure_v7_fields(conn)`，先 `PRAGMA table_info` 判断再 `ADD COLUMN`（可重复执行）。
- 读取侧统一由 `api_client` 提供取值助手（`api_field(row, "category.name")` / `api_updated_at(row)` / `api_youtube(row)`），**避免各处 `json.loads` 重复实现**；
- 现有列（`title`/`lyricist`/`composer`/`verse_*`/`chorus`/`audio_versions`/`*_img_path`…）继续按老路径写入 → **旧代码与旧查询零影响**，新增信息只在 `api_raw` 里。
- **`hymn_category` 用 API 重建整表**（决策 ⑥）：字段 `id / name（繁体）/ slug / hymn_count / updated_at`，由 `db.py::rebuild_hymn_category(records)` 先建临时表再原子替换（失败回滚），重建后打印行数与分布。
- UPSERT 策略沿用 v6：**空值不覆盖旧值**。
- `print_db_status` 增加「`api_raw` 覆盖数 / `hymn_category` 行数 / 音频条数 / 最新 `updated_at`」。
- 增量水位（§5.7）改从 `api_raw.updated_at` 解析（474 行 `json.loads` 实测 < 20 ms，无需额外列）。

### 5.7 增量同步算法（P1）

```
水位 W = max(json.loads(api_raw).updated_at)
拉全部 48 页（≈5–10 s）→ 逐首：
    rec.updated_at != db.api_raw.updated_at  → 需更新（歌词/元数据/资源 URL）
    rec.updated_at <= W                      → 仅校对，不动文件
    出现新资源版本                            → 追加到 probe_report 待下载队列
```
- 全量拉取成本已足够低，**无需**"只拉变动页"的复杂优化。

### 5.8 菜单变更（`crawler_fast.py`）

| 选项 | 现状 | 改后 |
| --- | --- | --- |
| 1 | Step 1 扫描（Selenium） | Step 1 扫描（**API**） |
| 2 | Step 2 提取（Selenium + 歌词 API） | Step 2 提取（**全 API**，并发） |
| 3 | 资源探测（PDF HEAD + 音频点击） | 资源探测（**API 清单** + HEAD 校验） |
| 7 | 全流程 | 全流程（API 版） |
| **10** | — | **极速全量同步（纯 API，含增量模式）** |
| 9 | 歌词重抓 | 保留（转为 `api_client` 薄封装） |

选项范围由 0–9 更新为 **0–10**（另加 `--engine api|selenium|auto`）。

### 5.9 异常与失败处理设计（#62 案例分析：如何保证"永不崩溃、不丢数据、不误报"）

#### 5.9.1 现状回放：#62「願主偕行」人聲版

| 环节 | 现行行为（代码位置） | 结果 |
| --- | --- | --- |
| 探测 | URL 写入 `probe_report.json.audio_versions["人聲版"]`（`probe.py`） | 与 API 给的 URL **完全相同** |
| 下载 | `download_one()` 非 200 → `return (item, False)`；`except Exception` 同（`downloader.py:94-105`） | 打印 `❌`，`fail += 1`，**continue，不抛异常** |
| 状态 | `_update_download_status()` 按"本地文件是否存在"重算 | `partial(3/4)` |
| 完整性 | `_verify_and_sync()` 逐文件校验 | `integrity_status = failed` |
| 入库 | `_backfill_paths_to_db()` 只写**本地存在**的版本 | DB `audio_versions` 只剩 `鋼琴版`（坏 URL 不入库） |
| 报告 | `verify.py` → `final_report.txt` | 缺失清单 + **硬编码**归档说明（`verify.py:301 if h == "62"`） |

**结论：现状不会因单个坏链崩溃**（DB 分布 `completed: 473 / partial(3/4): 1`），但存在 4 个缺陷：① 坏链永久计入期望分母 → 永远 `partial`+`failed`；② 失败原因不落盘；③ 无重试；④ 归档信息硬编码。

#### 5.9.2 改后：四层容错（任一层失败都不影响其余）

| 层 | 范围 | 机制 |
| --- | --- | --- |
| **L1 URL 级** | 单个资源 | `api_client`/`downloader` 内**退避重试**；4xx（非 429）**不重试**（判定为真缺失）；超时/SSL/连接重置/5xx/429 重试 `1.5^n` 秒，默认 3 次 |
| **L2 记录级** | 单首诗歌 | `validate_record()` 失败 → 该首降级 DOM（`USE_SELENIUM_FALLBACK=1`）或跳过并登记 `failed`，**不中断其它首**；`extractor` 沿用 `step2_progress.json` 断点 |
| **L3 阶段级** | 单个 Step | 阶段内 `try/except`，异常打印 + 写入错误清单 + 已落盘数据保留；提示可用续跑命令 |
| **L4 进程级** | 整程序 | `crawler_fast.main()` 顶层兜底（已有）+ `KeyboardInterrupt` 提示"可断点续跑" |

#### 5.9.3 资源可用性状态机（新增，写进 `probe_report.json`）

```
待检 pending → HEAD/Range 预检
   ├─ 2xx            → available → 进入下载队列 → 下载成功 → downloaded
   ├─ 404/403/410    → unavailable(4xx)   ← 记录 _http_status，**不计入期望集合**，不下载、不重试
   ├─ file_url=null  → unavailable(api_null)
   └─ 超时/5xx/SSL   → retry(≤3) → 仍失败 → unavailable(network)（下次运行自动重试）
```

字段放置（**零破坏兼容**）：全部挂在 `audio_versions[版本]` 与顶层 `_pdf_status` / `_unavailable` 等**下划线前缀键**上——现有代码 `downloader.py:61/221/303`、`crawler_fast.print_probe_report_status()` 均已过滤/忽略 `_` 前缀键，因此 `probe_report.json` **结构对旧消费者保持兼容**。

**期望集合规则**：`期望文件 = 仅 available 的资源`；`download_status` 仅在 available 资源真实缺失时降级。
效果：`#62 → completed`（并带 `_unavailable: {"人聲版": {"_http_status": 404}}`），不再永久 `partial`。
`verify.py` 的 `#62` 硬编码改为**数据驱动**：读取 `_unavailable` 汇总生成"失败任务归档"章节。

#### 5.9.4 后缀归一化（#201 实例）

`#201 人聲版` 的 URL 后缀是 `.mp4`（`video/mp4`，头部 `ftyp` → 即 MP4/M4A 容器）。若照抄后缀落盘为 `201_人聲版.mp4`：
- `verify.py` 的 `RESOURCE_EXTS = {".pdf", ".m4a", ".mp3"}` **不认 `.mp4`** → 该文件在多媒体对账中"消失"；
- `verify_file_integrity()` 走 `else` 分支（仅判 `size > 0`），校验强度下降。

对策：`to_audio_versions()` 把 `.mp4` → `.m4a`（同名容器，播放器/转换链路不受影响），并在 `RESOURCE_EXTS` 增加 `.mp4` 兜底。

#### 5.9.5 参数默认值（写入 `config.py`，可覆盖）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `API_MAX_WORKERS` | 8 | 实测 16 线程 11 req/s 无 429，8 更保守 |
| `API_RETRIES` / `API_BACKOFF` | 3 / 1.5 | `429/5xx/超时` 指数退避 |
| `PROBE_URL_CHECK` | `True` | 资源 URL 预检总开关（离线/极端省时可关） |
| `DOWNLOAD_RETRIES` / `DOWNLOAD_BACKOFF` | 2 / 2.0 | 下载阶段重试（仅瞬时类错误） |
| `USE_SELENIUM_FALLBACK` | `False` | 记录级 DOM 降级开关 |
| `CRAWL_ENGINE` | `"api"` | `api` / `selenium` / `auto` |

#### 5.9.6 不崩断言（P0 验收必测）

1. 构造坏 URL（404 / 空串 / 非法域名 / 超大延迟）→ 全链跑完，退出码 0，报告含 `_unavailable`；
2. 断网重跑 Step2 → 每首登记 `failed`，`step2_progress.json` 可续跑；
3. `file_url=null` 的 9 首（#178/#249/#255/#268/#274_b/#308/#386/#387/#389）→ 不产生任何下载请求；
4. API 返回结构被改（缺 `lyrics`）→ 触发降级或跳过，绝不 `KeyError` 崩栈。

---

## 6. 兼容性与迁移

| 产物 | 是否变化 | 保证措施 |
| --- | --- | --- |
| `url_map.txt`（`id\|目录名\|url`） | **不变** | 目录名沿用既有值；新增诗歌按同一 sanitize 规则生成 |
| `Hymn_Downloads/<目录>/` | **不自动重命名** | 标题变更只写报告、不自动迁移；**#349 已一次性人工迁移**为 `354_349奇妙的耶穌`（目录 + `url_map` + `probe_report` + `step5_progress` + DB 6 字段全链同步，迁移后三方对账仍 ✅） |
| 资源文件名（`{no}_五线谱.pdf` 等） | **不变** | 已实测命名规则 4/4 命中 |
| `probe_report.json` | **结构不变** | 字段/键名与现状一致，`downloader` 零改动 |
| `tjc_hymn` 表 | **只增 1 列** | v7 = `ADD COLUMN api_raw TEXT DEFAULT ''`，旧代码读旧列仍可用；`hymn_category` 表由 API **重建**（内容更新，结构兼容） |
| `Hymn_Downloads/api_cache/` | **新增（纳入 git 跟踪）** | 48 页 JSON 共约 2.2 MB，单文件 ≈46 KB（远低于「>5 MB 二进制禁提交」红线），可安全入库、便于离线对账 |
| `step2_progress.json` / `lyrics_progress.json` | 不变 | 断点续爬语义保留 |
| `checksums.json`（每首目录内） | 不变 | 下游未改动 |

**回滚方案**：所有改动以「开关 + 保留 Selenium 实现」方式落地——`USE_SELENIUM_FALLBACK=1` 或直接运行 `crawler_selenium.py` 即回到原 Selenium 全链；`selenium_legacy/` 代码原样保留、不删一行；DB 新列对旧逻辑无影响（可保留不用）。

---

## 7. 分阶段计划与验收标准

### P0 — 核心替换（预计 3–4 h，含测试）
交付：`api_client.py`、`naming.py`、`scanner.scan_api`、`extractor` API 主路径、`probe` API 清单 + URL 预检、`driver` 转发层 + `selenium_legacy/` 目录落位（不删代码）、`api_cache/` 落盘、`crawler_selenium.py` 保底入口。
验收：
1. Step 1 全量扫描 ≤ 15 s，且生成的 `url_map.txt` 与现状 **474 行完全一致**（#349 已迁移为 `354_349奇妙的耶穌`，474/474 符合命名规则）；
2. Step 2 全量 474 首 ≤ 3 min，`verse_1..10`/`chorus` 与现状**逐首 0 不一致**（对账脚本）；
3. `probe_report.json` 的 PDF 字段与现状**0 差异**；音频侧按**实测可用口径**核对：可用 **1119** 条、`_unavailable` 恰为 **10** 条（9 空 + #62 404），**无新增误报**；
4. **不崩断言**（§5.9.6 四项）全通过，退出码 0；
5. `--engine api` 下 `sys.modules` 不含 `selenium`；`crawler_selenium.py` / `--engine selenium` 仍可跑通旧链（保底可用）；
6. `api_cache/` 生成 48 个 JSON（合计 ≈2.2 MB）且二次运行命中缓存、不重复请求；
7. 现有 `pytest` 23 项全绿；`ruff` / `bandit` / `mypy` 门禁通过。

### P1 — 数据模型 + 增量（预计 2–3 h）
交付：DB v7 迁移（**单列 `api_raw`**）、`hymn_category` 用 API 重建、增量同步、`verify.py` 归档数据驱动化、残留数据修正（17 首 `composer`、#349 的元数据/音频对账——官网现仅剩 1 条 `鋼琴` mp3，本地 2 条已下架文件留档）。
> ✅ 用户拍板的**数据先行项已于 2026-09-12 完成**：136 个重复文件删除（-134.7 MB）、#201 `.mp4 → .m4a` 归一化（实测无需补下载）、#349 目录迁移 + DB 全链同步。
验收：
1. `pytest` 新增字段相关用例全绿；迁移可重复执行（幂等）；
2. 增量模式二次运行：0 首需要更新（水位判定正确）；
3. 输出「API vs 本地 DB」差异报表，人工确认后再落库；
4. `verify.py` 的 #62 硬编码归档改为 `_unavailable` 数据驱动，#62 状态归正为 `completed`；
5. `hymn_category` 重建后：分类数与 API 一致（45 类）、每类诗歌数与 API 逐类相符，且可重复执行（幂等）。

### P2 — 瘦身与保底固化（预计 1 h）
交付：Selenium 实现**移入 `crawler_core/selenium_legacy/`**（只搬不删）、`selenium` 移出主依赖到 `requirements-selenium.txt`、顶层 `crawler_selenium.py` **独立保底入口**、`selenium_legacy/README.md`、README 更新。
验收：
1. 全新环境（无 Chrome、未装 selenium）跑通 API 全流程；
2. `crawler_selenium.py`（或 `--engine selenium`）在装了 selenium 的环境下仍能跑通旧链（保底不失效）；
3. `pytest test/ -q` 全绿（`selenium_legacy/` 不参与默认测试）。

---

## 8. 测试计划

1. **离线单测**（`test/test_api_client.py`，不联网）：
   - 分页拼接（2 页 fixture → 顺序/数量正确）与 `total` 校验；
   - 字段映射：`lyrics → verses/chorus`、`history → source_info`（HTML 实体与换行）、`audio_files → {"鋼琴版": {...}}`；
   - `to_dirname` 与既有 474 条目录名的一致性（用 fixture 抽样）；
   - 重试退避：mock 429/500/超时 → 断言重试次数与最终结果；
   - `validate_record`：缺 `lyrics`/`sheet_score_pdf_url` 时应报告缺失字段。
2. **全量对账**（联网，一次性）：新库 vs 官网 API vs 旧库快照，五类差异表（歌词/副歌/PDF/音频/元数据），**0 差异才切换默认路径**。
3. **门禁**：`pytest test/ -q` + `ruff check` + `bandit -r crawler_core` + `mypy crawler_core`。
4. **实测基准脚本**：保留本次 `/tmp/bench_api_vs_selenium.py` 思路，纳入 `tool/`（如 `tool/bench_api.py`）便于回归。
5. **数据审计脚本**：把本轮 4 个取证脚本（`check_dup2.py` 重复文件全量 md5 对账、`verify_before_delete.py` 删除前引用核对、`reconcile_final.py` 终版音频对账、`apply_cleanup.py` 数据清理）整理进 `tool/data_audit/`，供以后站点改版后一键复检。
6. **`selenium_legacy` 测试**：以 `-m selenium` 标记隔离，仅在装有 Chrome 的环境运行，不阻塞 CI。

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 站点改版 / 内部 API 变更 | 全量失败 | 启动自检 `validate_record`；字段缺失自动降级 DOM（`USE_SELENIUM_FALLBACK=1`）；单测用 fixture 便于快速定位 |
| 触发限流 / 封 IP | 抓取中断 | 并发上限默认 8（实测 16 线程 11 req/s 无异常）；429/5xx 退避重试；失败清单落盘可续跑 |
| `verify=False` 的安全告警 | 门禁失败 | 沿用 `# nosec B501`（与 downloader 一致），并在注释说明自签名证书原因 |
| 349 等标题漂移 | 目录/文件路径错位 | 目录名以 `url_map.txt` 为准，**绝不自动重命名**；差异写入报告由人工决策（#349 已按决策迁移并全链同步） |
| 音频版本名映射错位 | 重复下载 / 命名冲突 | 映射规则 `API 名 + "版"` 已与现有文件名核对一致；命名统一走 `naming.py`；站点分类改名（合唱-N部 → 四部合唱-N部）遗留的 136 个重复文件已清理（§3.7） |
| **瞬时网络抖动（实测 33/2078 ≈ 1.6%）** | 静默丢 PDF/音频（现状无重试） | `api_client`/`downloader` 退避重试；失败清单落盘；复测机制（HEAD→GET Range） |
| **API 音频空记录/坏链（实测 10 条）** | 期望集合失真、永久 `partial` | URL 预检 + `_unavailable` 标记，**不计入期望**；`verify` 改为数据驱动；`#62` 状态归正 |
| **本地存量重复文件（136 个 / 134.7 MB）** | 统计口径混乱、磁盘浪费 | ✅ **已清理**（删除前逐对 md5 复核 + 引用核对）；清理脚本逻辑纳入 `tool/` 供回归（§3.7） |
| `.mp4` 后缀资源（#201） | 下游统计漏项（曾致"可补 1 条"误判） | ✅ **已归一化**为 `201_人聲版.m4a`（文件/checksums/probe_report/DB 四处同步）；新引擎 `to_audio_versions()` 统一归一化，`RESOURCE_EXTS` 加 `.mp4` 兜底 |
| Selenium 保底失效（Chrome 升级等） | 兜底不可用 | `selenium_legacy/` 保持可运行并单测（`-m selenium`）；保底非唯一手段：API 失败清单 + 断点续跑即可恢复 |
| 误把大文件提交 | 仓库膨胀 | `api_cache/`（约 2.2 MB JSON）**有意纳入跟踪**；仍严格禁止 >5 MB 二进制（遵循项目规则），提交前 `git status` 自查 |

---

## 10. 决策定稿（2026-09-12 用户拍板 10 项）

> 全部决策已确认，**P0 可直接开工**；其中 5 项属"数据先行"已在本次会话落地（见「落地状态」列）。

| # | 决策项 | 拍板结论 | 落地状态 |
| --- | --- | --- | --- |
| ① | Selenium 保底包命名 | **`crawler_core/selenium_legacy/`**（不用 `legacy/`） | 待 P0/P2 实施 |
| ② | 独立入口 | **需要 `crawler_selenium.py` 作为独立备选入口**（必备，非可选） | 待 P2 实施 |
| ③ | #349 处理 | **新建「354_349奇妙的耶穌」并迁移文件，数据库同步修改** | ✅ **已完成**（目录 + `url_map.txt` + `probe_report.json` + `step5_progress.json` + DB 6 字段；三方对账仍 ✅） |
| ④ | 音频补齐范围 | **补 #201 那 1 条音频** | ✅ **已核实无需下载**：本地已有且与远端 md5 完全一致（`c6b9c8fc…`）→ 实际只做 `.mp4 → .m4a` 归一化 |
| ⑤ | DB v7 字段形态 | **只加一列 `api_raw`**（API 原始记录 JSON） | 待 P1 实施（§5.6 已按此重写） |
| ⑥ | `hymn_category` 表 | **用 API 重建整表** | 待 P1 实施（§5.6） |
| ⑦ | 缓存策略 | **48 页响应落盘 `Hymn_Downloads/api_cache/`，不 gitignore，2.2 MB 入库可接受** | 待 P0 实施（§6 兼容表已改） |
| ⑧ | 存量重复文件 | **先与官网数据对账；确认重复则删除** | ✅ **对账确认 + 已删除**：API 侧这 34 首只有 `四部合唱-N部`（无 `合唱-N部`），136 对文件全文件 md5 完全相同且未被 `probe_report`/DB 引用 → 删除 136 个文件（-134.7 MB），同步 `checksums.json`；#6 的 4 个（API 真实分类）保留 |
| ⑨ | 不可用资源的 `download_status` 语义 | **采用推荐方式**：不计入期望集合 → `#62 → completed` + `_unavailable` 标注 | 待 P0/P1 实施（§5.9.3） |
| ⑩ | `.mp4 → .m4a` 归一化 | **同意**（前提：不影响播放——同容器改名，播放/转换链路不受影响） | ✅ **已完成**（`201_人聲版.m4a`：文件 + `checksums.json` + `probe_report.ext` + DB 路径四处同步） |

**拍板后的新增待办**（原表之外，由本轮对账派生）：
- #349 的 2 条已下架文件（`349_人聲版.mp3`、`349_四部合唱版.m4a`）**不是重复文件 → 保留留档**，P1 对账报表中标注 `site_removed`；
- #349 官网现仅剩 1 条 `鋼琴` 音频，且 URL 已换为 `…af2877e3….mp3`（本地为旧 `…2fbccdb4….m4a`）→ P1 决定是否按新 URL 重下/替换（建议：先比对时长与码率，再决定是否替换）。

---

## 附录 A · 本次取证脚本（均为只读，位于 `/tmp`）

| 脚本 | 用途 |
| --- | --- |
| `api_explore.py` | 探测端点、字段、SSR 情况、robots |
| `api_explore2.py` | 全量 48 页拉取 + 覆盖率 + 与 DB/probe 对账 |
| `api_explore3.py` / `api_explore4.py` | 元数据差异、音频逐首对账、分类表结构 |
| `api_explore5.py` | 从 JS bundle 枚举端点 + 并发限流测试 + `per_page` 上限 |
| `bench_api_vs_selenium.py` | 速度基准（Selenium vs API） |
| `check_dir_naming.py` / `check_dir_naming2.py` | 目录名/文件名可推导性与顺序一致性 |
| `check_list_vs_detail.py` | 列表项与详情记录等值性 |
| `check62.py` | #62 全链取证：API/probe_report/DB/本地文件/HTTP 实测（本轮新增） |
| `check_urls.py` | 全量 2078 个资源 URL 可达性实测（16 线程 HEAD，本轮新增） |
| `check_urls2.py` | 非 200 复测（重试 + GET Range 兜底）+ `file_url` 字段形态检查（本轮新增） |
| `reconcile_audio.py` | 以"实测可下载"为准的 API vs 本地逐版本对账（本轮新增） |
| `check_seq.py` | `seq` 与 `hymn_number` 偏差、目录/文件命名核对（本轮新增） |
| `check_detail.py` | 详情接口 vs 列表接口音频差异复核（本轮新增） |
| `check_dup.py` | `合唱-N部版` 与 `四部合唱-N部版` 内容比对 + #201 下载实测（本轮新增） |
| `check_dup2.py` | **全量**重复文件对账：API 分类名统计 + 34 首 × 4 部全文件 md5 + 本地配对分析（本轮新增） |
| `verify_before_delete.py` | 删除前核验：`probe_report`/DB/`checksums.json` 引用关系 + #201 本地与远端 md5 比对（本轮新增） |
| `reconcile_final.py` | **终版音频对账**（修正 `.mp4` 盲点）：期望（实测可用 1119）vs 本地 1121，输出缺失/多余清单（本轮新增） |
| `apply_cleanup.py` | 数据清理执行脚本：删重复文件（逐对 md5 守卫）+ `.mp4→.m4a` + `#349` 目录迁移（本轮新增） |

产出数据：`/tmp/api_all_hymns.json`（474 首全量 2.2 MB，供离线复现对账）

## 附录 B · 关键样例（12 号「耶穌尊名」）

```json
{
  "no": "12", "name": "耶穌尊名",
  "lyrics": [{"text": "耶穌尊名入我耳中，好像和諧美妙樂聲；\r\n願傳此名，音大聲洪，天上地下都聽。"}, "...（共 4 节）"],
  "lyrics_chorus": "美哉，大哉，耶穌我主！創造救贖，權能無比；\r\n離去天上，降生世間，捨身替我受死。",
  "sheet_score_pdf_url": "https://sacredmusic.tjc.org.tw/storage/uploads/hymn/score/sheet/12.pdf",
  "num_score_pdf_url": "https://sacredmusic.tjc.org.tw/storage/uploads/hymn/score/num/12.pdf",
  "audio_files": [
    {"file_url": ".../storage/uploads/hymn/audio/12.m4a", "audio_category": {"name": "鋼琴"}},
    {"file_url": ".../storage/uploads/hymn/audio/14db45f5....m4a", "audio_category": {"name": "人聲"}}
  ],
  "history": "<p>...十八世紀的前半世紀，英國教會...</p>",
  "lyricists": [{"name": "Issac Watts"}], "composers": [{"name": "Austin C. Lovelace"}],
  "category": {"id": 2, "name": "讚美耶穌"},
  "youtube_urls": [{"label": "12 - 耶穌尊名", "url": "https://www.youtube.com/watch?v=d-C-gMw2VI4..."}],
  "prev_no": "11", "next_no": "13",
  "updated_at": "2023-07-26T03:08:19.000000Z"
}
```

