# 官网 JSON API 重构方案（设计文档 · 待评审）

> 版本：v1（2026-09-12）　状态：**待评审，未实施**
> 关联会话日志：`docs/sessions/2026-09-12_21-47-42.md`（任务 2）
> 结论一句话：**可行，收益 ≈10×；建议 P0 → P1 → P2 分三阶段推进，P0 完成即可摆脱对 Selenium 的性能依赖**

---

## 0. TL;DR

| 维度 | 现状（Selenium） | 改后（官网 API） |
| --- | --- | --- |
| Step 1 列表扫描 | 48 页 × 2.76 s ≈ **2.2 min** | 48 请求 ≈ **5 s**（6 线程） |
| Step 2 详情（474 首） | 实测 2.27 s/首 ≈ **17.9 min** | 实测 0.21 s/首（10 线程）≈ **100 s** |
| 资源探测 · PDF | 948 次 HEAD ≈ 0.5 min | URL 由 API 直接给出（HEAD 校验保留） |
| 资源探测 · 音频 | 474 页逐个点击播放 ≈ **8–10 min** | **0 s**（随数据一起返回） |
| **合计（元数据 + 资源探测）** | **≈ 28–30 min** | **≈ 2–3 min** |
| 数据完整性 | 音频 473 首 / 1120 条 | **474 首 / 1130 条**（多 11 首） |
| 增量更新 | 只能全量重扫 | 依 `updated_at` 真增量 |
| 运行依赖 | Chrome + chromedriver 版本匹配 | 纯 requests |

关键前提（已实测）：**列表接口 `/api/hymn` 每条记录 = 详情接口 `/api/hymn/{no}` 的完整记录**（仅详情多 `prev_no`/`next_no`），因此**全量 474 首只需 48 个请求、2.2 MB**。

---

## 1. 背景与目标

### 1.1 现状痛点
1. **慢**：Step 2 单线程 2.27 s/首 × 474 ≈ 18 min；资源探测又要 474 次页面点击播放（音频 URL 只能靠 `audio#player` 的 src 拿到）。
2. **脆**：依赖 Chrome / chromedriver 版本匹配；页面为**客户端渲染**（原始 HTML 里 `div.music` / `.lyrics_box` / `music_title` 均无数据），DOM 结构一变就全挂；音频探测靠 `time.sleep(1.5)` 等异步播放器加载，偶发漏抓（本次音频少 11 首即此原因）。
3. **信息有损**：仅能拿到页面展示的字段；官网 API 里现成的 `category` / `tags` / `composers` / `lyricists` / `youtube_urls` / `prev_no` / `updated_at` 全部拿不到（或拿不全会）。
4. **无法增量**：没有 `updated_at`，只能全量重扫。

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
| 音频 | 474 首 / 1130 条 | 473 首 / 1120 条 | **API 多 11 首 12 条**：62、122、178、249、255、268…（122「彼此相愛」probe 完全未抓到）；DB 多 1 首（349，官网已下架该版本） |
| 歌词 / 副歌 | 474 / 270 | 474 / 270 | **逐首 0 不一致**（本次修复后） |
| 元数据 | — | — | 17 首 `composer` DB 为 `Unknown` 而 API 有名字；349 标题/词作者 API 为新值；`source_info` 与 `history` 同源（差异仅 HTML 实体与换行） |

音频版本名（API 分类名 + `版` = 现有文件名，**规则已实测吻合**）：

```
API : 鋼琴 / 人聲 / 四部合唱 / 合唱-1部 … 四部合唱-4部
文件: {no}_鋼琴版.m4a / {no}_人聲版.mp3 / {no}_四部合唱版.m4a / {no}_合唱-1部版.* …
```

（分布：鋼琴 475、人聲 469、四部合唱 46、四部合唱-1..4 部各 34、合唱-1..4 部各 1）

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
| 目录名 == `f"{seq:03d}_{sanitize(no+name)}"`（sanitize 沿用 `Scanner._create_dir`：仅保留字母数字与空格/下划线/连字符） | **473/474**；唯一例外 **349**（官网标题由「救主正在等候」改为「奇妙的耶穌」） |
| 文件名规则（`{no}_五线谱.pdf` / `{no}_简谱.pdf` / `{no}_{版本}版.{ext}`） | 抽查 4 首 **全部命中** |

→ 兼容策略：**目录名以现有 `url_map.txt` 为准，API 只做「校验 + 新增」**（349 保持旧目录名，不重命名文件），新增诗歌按同一 sanitize 规则生成。

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
| `crawler_core/driver.py` | 修改 | Selenium 依赖**改为函数内延迟导入**（无 Chrome 环境也能跑纯 API 流程） |
| `crawler_core/db.py` | 修改 | v7 迁移（幂等 `ADD COLUMN`）+ 新字段写入 + 状态打印扩展 |
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
| `history`（HTML） | `source_info` | `html.unescape` → `<br>`/`</p><p>` → `\n` → 去标签 → `strip()`；另存原 HTML 到 `history_html`（P1） |
| `sheet_score_pdf_url` | `probe_report.staff_pdf` + `sheet_score_pdf_url`（P1） | 原样 |
| `num_score_pdf_url` | `probe_report.numbered_pdf` + `num_score_pdf_url`（P1） | 原样 |
| `audio_files[].file_url` | `probe_report.audio_versions[版本+版]` | 版本名 = `audio_category.name + "版"`；`filename` = URL 末段；`ext` = 后缀 |
| `category.name` | `category`（P1） | 繁体分类名（45 类） |
| `tags[].name` | `tags`（P1） | JSON 字符串（默认空数组） |
| `youtube_urls` | `youtube_urls`（P1） | JSON 字符串 |
| `updated_at` | `api_updated_at`（P1） | 增量同步水位 |
| `prev_no` / `next_no` | `prev_no` / `next_no`（P1） | 仅详情接口返回 |

`probe_report.json` 的结构与键名**完全不变**，因此 `downloader.py` 无需改动。

### 5.2 `scanner.py`（Step 1）

```python
class Scanner:
    def scan_api(self) -> list[dict]:        # 默认：48 请求拿全量 → song 列表 → 建目录 → 写 url_map
    def scan_legacy(self) -> list[dict]:     # 原 Selenium 翻页逻辑（保留，开关启用）
```
- `_create_dir` / `_save_map` / `_load_existing` **逻辑与 sanitize 规则完全保持不变**（保证历史目录不重建）。
- 新增校验：若 `f"{seq}_{sanitize(no+name)}"` 与 `url_map.txt` 既有目录名不符（如 349），**沿用既有目录名并打印提示**，仅登记差异不重命名。
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
- 原 `_capture_song_audio` 改名 `_capture_song_audio_legacy`，仅在 API 无 `audio_files` 时用于单首兜底。
- `run_probe_missing()`（增量补探音频）语义改为「API 与本地清单的差集」，接口保留。

### 5.5 `driver.py` 与依赖

```python
def init_driver():
    from selenium import webdriver          # 延迟导入：无 Chrome 环境也能跑纯 API 流程
    ...
```
- P0/P1 阶段 `selenium` 仍留在 `requirements.txt`（兜底用），P2 阶段再移除。

### 5.6 DB v7 迁移（幂等，向后兼容）

```sql
ALTER TABLE tjc_hymn ADD COLUMN category TEXT DEFAULT '';
ALTER TABLE tjc_hymn ADD COLUMN tags TEXT DEFAULT '[]';
ALTER TABLE tjc_hymn ADD COLUMN youtube_urls TEXT DEFAULT '[]';
ALTER TABLE tjc_hymn ADD COLUMN history_html TEXT DEFAULT '';
ALTER TABLE tjc_hymn ADD COLUMN sheet_score_pdf_url TEXT DEFAULT '';
ALTER TABLE tjc_hymn ADD COLUMN num_score_pdf_url TEXT DEFAULT '';
ALTER TABLE tjc_hymn ADD COLUMN api_updated_at TEXT DEFAULT '';
ALTER TABLE tjc_hymn ADD COLUMN prev_no TEXT DEFAULT '';
ALTER TABLE tjc_hymn ADD COLUMN next_no TEXT DEFAULT '';
```
- 沿用 v6 的 `ensure_chorus_field` 范式：新增 `ensure_v7_fields(conn)`，先 `PRAGMA table_info` 判断再 `ADD COLUMN`（可重复执行）。
- UPSERT 策略沿用 v6：**空值不覆盖旧值**。
- `print_db_status` 增加「分类覆盖 / YouTube 覆盖 / 音频条数 / `api_updated_at` 最新值」。

### 5.7 增量同步算法（P1）

```
水位 W = max(api_updated_at)
拉全部 48 页（≈5–10 s）→ 逐首：
    rec.updated_at != db.api_updated_at  → 需更新（歌词/元数据/资源 URL）
    rec.updated_at <= W                  → 仅校对，不动文件
    出现新资源版本                        → 追加到 probe_report 待下载队列
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

选项范围由 0–9 更新为 **0–10**。

---

## 6. 兼容性与迁移

| 产物 | 是否变化 | 保证措施 |
| --- | --- | --- |
| `url_map.txt`（`id\|目录名\|url`） | **不变** | 目录名沿用既有值；新增诗歌按同一 sanitize 规则生成 |
| `Hymn_Downloads/<目录>/` | **不重命名** | 349 等标题变更的诗歌保持旧目录名（差异写入报告） |
| 资源文件名（`{no}_五线谱.pdf` 等） | **不变** | 已实测命名规则 4/4 命中 |
| `probe_report.json` | **结构不变** | 字段/键名与现状一致，`downloader` 零改动 |
| `tjc_hymn` 表 | **只增列** | v7 全部为 `ADD COLUMN` + `DEFAULT`，旧代码读旧列仍可用 |
| `step2_progress.json` / `lyrics_progress.json` | 不变 | 断点续爬语义保留 |
| `checksums.json`（每首目录内） | 不变 | 下游未改动 |

**回滚方案**：所有改动以「开关 + 保留 legacy 函数」方式落地——`USE_SELENIUM_FALLBACK=1` 即回到原 Selenium 路径；DB 新增列对旧逻辑无影响（可保留不用）。

---

## 7. 分阶段计划与验收标准

### P0 — 核心替换（预计 3–4 h，含测试）
交付：`api_client.py`、`scanner.scan_api`、`extractor` API 主路径、`probe` API 清单、`driver` 延迟导入。
验收：
1. Step 1 全量扫描 ≤ 15 s，且生成的 `url_map.txt` 与现状 **474 行完全一致**（349 沿用既有目录名，差异仅写入日志/报告，不落盘）；
2. Step 2 全量 474 首 ≤ 3 min，`verse_1..10`/`chorus` 与现状**逐首 0 不一致**（对账脚本）；
3. `probe_report.json` 的 PDF 字段与现状**0 差异**；音频条目 ≥ 现状（预期 +12 条）；
4. 现有 `pytest` 23 项全绿；`ruff` / `bandit` / `mypy` 门禁通过。

### P1 — 数据模型 + 增量（预计 2–3 h）
交付：DB v7 迁移、新字段写入、增量同步、数据修正（17 首 composer、349 元数据、11 首缺失音频入下载队列、`hymn_category` 重建方案）。
验收：
1. `pytest` 新增字段相关用例全绿；迁移可重复执行（幂等）；
2. 增量模式二次运行：0 首需要更新（水位判定正确）；
3. 输出「API vs 本地 DB」差异报表，人工确认后再落库。

### P2 — 瘦身（预计 1 h，需你确认）
交付：移除 `selenium` 依赖与 `driver.py`（或降级为 `extras/`）；README 更新；CI/环境说明更新。
验收：全新环境（无 Chrome）跑通全流程。

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

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 站点改版 / 内部 API 变更 | 全量失败 | 启动自检 `validate_record`；字段缺失自动降级 DOM（`USE_SELENIUM_FALLBACK=1`）；单测用 fixture 便于快速定位 |
| 触发限流 / 封 IP | 抓取中断 | 并发上限默认 8（实测 16 线程 11 req/s 无异常）；429/5xx 退避重试；失败清单落盘可续跑 |
| `verify=False` 的安全告警 | 门禁失败 | 沿用 `# nosec B501`（与 downloader 一致），并在注释说明自签名证书原因 |
| 349 等标题漂移 | 目录/文件路径错位 | 目录名以 `url_map.txt` 为准，绝不重命名；差异写入报告并由人工决策是否迁移 |
| 音频版本名映射错位 | 重复下载 / 命名冲突 | 映射规则 `API 名 + "版"` 已与现有 1120 条文件名核对一致 |
| 误把大文件/缓存提交 | 仓库膨胀 | `api_cache/`、`probe_report.json` 等加入 `.gitignore`；遵循「>5 MB 二进制禁提交」规则 |

---

## 10. 待决策项（请评审时确认）

1. **Selenium 的最终去留**：P2 是否删除 `driver.py` + `selenium` 依赖？（建议：P0/P1 稳定跑 1–2 周后再删）
2. **349 的处理**：保留旧目录名「354_349救主正在等候」（推荐）还是新建「354_349奇妙的耶穌」并迁移文件？
3. **DB v7 字段形态**：按 5.6 逐字段建列（查询友好，推荐）还是只加一列 `api_raw`（JSON 全量存档，最省事）？
4. **`hymn_category` 表**：保留现有简体 45 行（来自 `merged_all.json`）不动、新分类写 `tjc_hymn.category`（推荐），还是用 API 重建整表？
5. **缓存策略**：是否将 48 页响应落盘到 `Hymn_Downloads/api_cache/`（离线复现/对账更方便，但会多 2.2 MB 本地文件，需 gitignore）？
6. **音频补齐范围**：是否将 API 多出的 11 首 12 条音频加入下载队列（约 +10–20 MB）？

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

