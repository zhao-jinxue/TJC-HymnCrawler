# hymn_crawler 开发总结（SESSION_SUMMARY）

> 用途：新会话续接开发的最小上下文入口。更新于 2026-09-19（音频时长入库 v10）。

## 项目状态
- TJC 赞美诗（hymn）数据爬虫 + 数据处理流水线，已完成：探测 → 下载 → 提取 → 转图 → 校验 → 入库全流程
- **目录约定（2026-09-13 重排）**：根目录只保留 `README.md` / `LICENSE` / `crawler_api.py` / `tjc_hymn.db`；
  `config/`（依赖 + 门禁/测试配置）、`data/`（`probe_report.json`、`final_report.txt`）、`legacy/`（Selenium 保底入口）
- **默认引擎 = 官网 JSON API**（纯 `requests`，零浏览器依赖）：`crawler_api.py`（`--engine api|selenium|auto`，原 `crawler_fast.py`）
- **Selenium 保底引擎**完整保留：`crawler_core/selenium_legacy/` + 独立整链入口 `legacy/crawler_selenium.py`
- 核心模块：`crawler_core/`（`api_client` / `naming` / `scanner` / `extractor` / `probe` / `sync` / `downloader` /
  `verify` / `db` + `selenium_legacy/`）、`tool/`（数据处理 + `data_audit/` 审计 + `show_lyrics.py` 歌词复核）、`test/`（81 项 pytest）
- 数据文件：`tjc_hymn.db`（SQLite **v10** 主表 474 首 29 列，含副歌 `chorus`、API 原始记录 `api_raw`、
  **音频时长 `audio_durations`（版本名 → 秒，与 `audio_versions` / `audio_version_list` 键集一一匹配）**；
  另含 **v8 两表** `hymn_jianpu` / `hymn_jianpu_line`——PPT 带简谱文字歌词 474 份 / 12136 行；
  以及 **v9 五表** `hymn_score` / `hymn_score_line` / `hymn_score_lyric` / `hymn_score_char` /
  `hymn_codepoint_map`——官方简谱 PDF 的「曲谱 + 歌词 + 拍位 + 逐字对应」473 首 / 7914 谱行 / 5481 歌词行 / 27567 字）、
  `data/probe_report.json`、`data/final_report.txt`、`data/jianpu_report.txt`、`Hymn_Downloads/api_cache/`（48 页 API 缓存，已入 git）
- 数据口径：三方对账 **474/474/474**；资源 **2067/2067 完整（100%）**；音频可用 **1119** 条 / 474 首；
  **音频时长全量读出 1119/1119 条（合计 47h59m）**；
  源站不可用 **10** 条（9 条空记录 + #62 404，已摘出期望集合）；`download_status` 全量 `completed`
- 副歌口径（2026-09-13 实测复核）：库内 **270 首有副歌 / 204 首无**，与**实时**官网 API（绕过 `api_cache`）逐字一致；
  无副歌的 204 首在官网列表接口与详情接口中 `lyrics_chorus` 均为空（站点本身无副歌），非抓取丢失
- 官方 API：`/api/hymn?page=N`（列表=详情，48 页 474 首）、`/api/hymn/{no}`（详情，多 `prev_no/next_no`）

## 最近主要工作
1. 全流程流水线打通（选项 7 全自动，已有内容自动跳过）
2. 修复歌词采集缺陷：旧版每个 Tab 只取首个 `.lyrics_box`，导致 270 首副歌整段丢失 → `lyrics_api.py` + DB v6 `chorus`
3. **API 重构（2026-09-12/13）**：`api_client.py`（重试退避 + 分页缓存 + 字段映射 + 可用性状态机）、`naming.py`（命名真源）、
   `scanner.scan_api`、`extractor` API 并发主路径、`probe` API 清单 + URL 预检、`driver.py` 转发层 + `selenium_legacy/`、
   DB v7（`api_raw`）+ `hymn_category` API 重建、`sync.py` 增量同步、`verify.py` 归档数据驱动、依赖拆分与 `crawler_selenium.py`
4. 实测提速：Step 1 **5.9 s**（原 ≈2.2 min）、Step 2 **58.7 s**（原 ≈18 min）、资源探测 **95.8 s**
   （原音频需 474 次页面点击 ≈8–10 min）
5. 数据修正：#349（官网换诗：旧 PDF/音频曾移入 `_archive/` 留档 → **2026-09-13 已删除该归档，并由新 PDF 重出简谱/五线谱 PNG**）、#62 归正 `completed`、
   #25/#31 等 composer 修正、#201 `.mp4 → .m4a`、清理 136 个重复文件（134.7 MB）
6. **根目录重排（2026-09-13）**：`crawler_fast.py` → `crawler_api.py`、`crawler_selenium.py` → `legacy/`、
   产物 → `data/`、配置 → `config/`、计划文档 → `docs/`；同步修正 `config.py`/`db.py`/`verify.py` 路径常量与全部文档/门禁命令
7. **VSCode(pyright) 诊断清零 + 副歌口径复核（2026-09-13）**：修 `api_client`/`extractor`/`sync` 三处告警
   （`sync.pending_downloads` 另含一处真实 bug，见会话日志），并以实时 API 复核副歌 270/204 口径
8. **归档清理 + 库表列顺序对齐（2026-09-13）**：删除 #349 `_archive/`（5 个旧资源 + README + md5）并由新 PDF
   重出简谱/五线谱 PNG；新增 `tool/reorder_table_columns.py` 按 `_create_table_v4` 重建 `tjc_hymn` 列顺序
   （28 列、474 行数据零差异、`sqlite_sequence` 保持）；`checksums.json` 全量重建（顺带补齐 34 个历史遗漏目录）
9. **PPT 带简谱文字歌词入库（2026-09-14）**：论证并实现 `data/赞美诗PPT/*.ppt`（474 份）→ DB。
   取证结论：PPT 正文里**每张幻灯片=一节**，行序「标题 →(副歌)/(三) 标签 →简谱记号行+歌词行若干对 →`k/M` 节号」；
   记号是**纯 ASCII**（`1-7` 音级、字母=数字+减时线/八度点合成字形、`/`=延长线、`\`=小节线、零宽码位=修饰），
   由 `简谱字体.ttf`（01SMN）渲染（未装字体时"看不到字"）——字形语义逐个落图与官方简谱比对确认。
   新增模块 `crawler_core/ppt_jianpu.py`（解析 + 5 项校验 + 报告）、工具 `tool/extract_jianpu.py`（写库）与
   `tool/show_jianpu.py`（渲染复核，出图与官方简谱并排）；DB 新增 **v8 两表** `hymn_jianpu`（主键 `ppt_file`，
   容纳甲/乙版本）+ `hymn_jianpu_line`（每行 `notes`/`lyric` + 音符数/字数/等长标志）。
   实测：474 份全部解析（0 失败）、映射 473 首（1 首 DB 无对应）、**404 首全项通过 / 70 首带复核标记**、
   12136 行中 7309 行严格等长 + 4807 行一字多音 + 20 行音符数不足；跨节曲调比对**检出 41 首作者记谱手误**
10. **官方简谱曲谱入库（2026-09-15）**：以**网站标准源**（`Hymn_Downloads/*/N_简谱.pdf`，矢量文本）为准——
    新增 `crawler_core/pdf_score.py`（PDF → 乐句 / 谱层 / 主旋律层 / 歌词块 / 拍位栅格 / 逐字对位 / 等长校验）、
    `tool/build_score.py`（`--learn` / `--only` / `--limit` / `--dry-run` / `--stats` / `--show`）与
    DB **v9 五表**（`hymn_score` / `hymn_score_line` / `hymn_score_lyric` / `hymn_score_char` /
    `hymn_codepoint_map`，不改动 `tjc_hymn` 与 v8 两表）。实测：**473 首入库**（一轮 ≈18 秒、幂等）、
    7914 谱行、5481 歌词行、27567 逐字，逐字几何对位可靠 **99.0%**；`count_delta = 音符数 − 字数`
    让「节拍与歌词等长」可一条 SQL 校验（964 行严格等长 / 1035 行一字多音 / 21 行音符数不足）。
    **同时修掉一个系统性错配 bug**：目录名首位是**网站列表序号**而非诗歌编号（`339_334耶穌沙崙玫瑰`），
    旧 `pdf_path()` 按编号匹配目录前缀，会把 #334 解析成 #329「天父我神」（40 首抽样错配 16 首、命中 0 处），
    改为按文件名匹配后同批样本命中 **58 处**。另确认 **#349 是站点上传的异版 PDF**（Type3 字形、无文本层、
    尺寸 420×595），**非文件损坏、无需重下**，需 OCR 或从 v8 侧补齐（本轮以 `review_reason` 显式记录）
11. **音频时长入库（2026-09-19，DB v10）**：新增 `tjc_hymn.audio_durations`（JSON：版本名 → 秒），
    与 `audio_versions` / `audio_version_list` **键集一一匹配**（读不出的版本值落 `null`，键保留）。
    新增 `crawler_core/audio_duration.py`（mutagen **按后缀显式分派** `MP3`/`MP4`——`mutagen.File()` 的类型
    嗅探对无 ID3 标签的 MP3 返回 None，本库 #138/#197/#296_b 人聲版即此例）+ `tool/build_audio_durations.py`
    （`--only/--limit/--dry-run/--stats/--show`，幂等可重跑）。实测 **474 首 / 1119 条全部读出（0 失败）**、
    合计 **47h59m**（27.2–389.2 秒，中位 154.5）、键集不一致 **0 行**；列序由 `tool/reorder_table_columns.py`
    重建对齐（29 列，`audio_durations` 紧跟 `audio_version_list`）。

## 遗留任务（可选，未排期；详见 `docs/API_REFACTOR_PLAN.md` §7「P3 — 展望」）
- ✅ 已执行（不再是待决策项）：删除 `#349` 的 `Hymn_Downloads/_archive/`（5 个旧资源 + README + md5），并由新 PDF 重出简谱/五线谱 PNG
- 🔧 可选增强：`tool/bench_api.py` 基准回归脚本、`selenium_legacy` 的 `-m selenium` 用例（当前仅标记注册、无用例）
- 🔧 可选同步：`merged_all.json` 的 category 数据落库核对（`hymn_category` 现由 API 重建为 **47 类**）；
  导出脚本（`tool/merge_to_json.py` / `json_to_db.py`）尚未纳入 `chorus` / `api_raw` 字段
- 🧑‍⚖️ **待确认**：歌词「缺副歌」反馈 —— 已证库内与实时官网 1:1 一致（270 有 / 204 无），
  若要为 204 首补副歌需另开「PDF/OCR 提取」路线（现 OCR 工具在 `tool/`）
- 🎼 **官方简谱曲谱（v9）后续**：① #349 走 OCR（`tool/qwen_ocr.py`）或从 v8 侧补齐；
  ② 码位映射扩充（库学 27 个 + 人工种子 14 个，仍约四成行记号含 `?`；瓶颈是「PPT 一行 4 小节 vs
  官方谱一行 6 小节」的整行同构匹配 → 下一步做**拍位级投票**，见 `docs/sessions/2026-09-15_21-09-00.md` 任务 2）；
  ③ 无词乐句（约四成）与 v8 / 五线谱交叉校验，确认是间奏还是「第二段旋律」
- 可能的 UI / 前端展示层（尚未开始）

## 环境与命令
- Python 环境：`/home/zjx/python_env/bin/python`（opencc / rapidocr 等依赖）
- 主依赖：`pip install -r config/requirements.txt`；保底引擎：`pip install -r config/requirements-selenium.txt`（+ Chrome/chromedriver）
- 运行：`python crawler_api.py`（交互）/ `python crawler_api.py --engine api --step 1`（非交互）；保底：`python legacy/crawler_selenium.py`
- 校验：`python crawler_api.py --step check`（三方一致）、`python -m crawler_core.verify`（全量对账 + `data/final_report.txt`）
- 歌词复核：`python tool/show_lyrics.py 12` / `--stats` / `--no-chorus`（看正歌+副歌并与 `api_raw` 逐字比对）
- 带简谱歌词（`data/赞美诗PPT/` 就位时）：`python tool/extract_jianpu.py --dry-run`（只出报告）/
  `python tool/extract_jianpu.py`（写 `hymn_jianpu` + `hymn_jianpu_line`）/ `python tool/show_jianpu.py 1 --map`（渲染复核）
- 音频时长（v10 `audio_durations`）：`python tool/build_audio_durations.py`（全量统计写库，幂等）/
  `--only 1 5 349` / `--limit 20 --dry-run` / `--stats` / `--show 1`；重下或替换音频后需重跑
- 测试与门禁：`python -m pytest -c config/pytest.ini test/ -q`、`ruff check --config config/ruff.toml .`、
  `mypy crawler_core crawler_api.py legacy/crawler_selenium.py`、`bandit -c config/bandit.yaml -r crawler_core crawler_api.py legacy/crawler_selenium.py`
- 详细方案：`docs/API_REFACTOR_PLAN.md`；上下文最小化：`docs/CLINE_CONTEXT_MINIMIZE.md`；会话日志：`docs/sessions/`
