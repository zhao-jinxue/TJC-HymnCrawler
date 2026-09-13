# hymn_crawler 开发总结（SESSION_SUMMARY）

> 用途：新会话续接开发的最小上下文入口。更新于 2026-09-13（API 重构 P0/P1/P2 完成 + 根目录重排）。

## 项目状态
- TJC 赞美诗（hymn）数据爬虫 + 数据处理流水线，已完成：探测 → 下载 → 提取 → 转图 → 校验 → 入库全流程
- **目录约定（2026-09-13 重排）**：根目录只保留 `README.md` / `crawler_api.py` / `tjc_hymn.db`；
  `config/`（依赖 + 门禁/测试配置）、`data/`（`probe_report.json`、`final_report.txt`）、`legacy/`（Selenium 保底入口）
- **默认引擎 = 官网 JSON API**（纯 `requests`，零浏览器依赖）：`crawler_api.py`（`--engine api|selenium|auto`，原 `crawler_fast.py`）
- **Selenium 保底引擎**完整保留：`crawler_core/selenium_legacy/` + 独立整链入口 `legacy/crawler_selenium.py`
- 核心模块：`crawler_core/`（`api_client` / `naming` / `scanner` / `extractor` / `probe` / `sync` / `downloader` /
  `verify` / `db` + `selenium_legacy/`）、`tool/`（数据处理 + `data_audit/` 审计 + `show_lyrics.py` 歌词复核）、`test/`（81 项 pytest）
- 数据文件：`tjc_hymn.db`（SQLite **v7**，474 首，含副歌 `chorus` 与 API 原始记录 `api_raw`）、`data/probe_report.json`、
  `data/final_report.txt`、`Hymn_Downloads/api_cache/`（48 页 API 缓存，已入 git）
- 数据口径：三方对账 **474/474/474**；资源 **2067/2067 完整（100%）**；音频可用 **1119** 条 / 474 首；
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
5. 数据修正：#349（官网换诗：旧 PDF/音频移入 `_archive/` 留档 + 按新 URL 重下）、#62 归正 `completed`、
   #25/#31 等 composer 修正、#201 `.mp4 → .m4a`、清理 136 个重复文件（134.7 MB）
6. **根目录重排（2026-09-13）**：`crawler_fast.py` → `crawler_api.py`、`crawler_selenium.py` → `legacy/`、
   产物 → `data/`、配置 → `config/`、计划文档 → `docs/`；同步修正 `config.py`/`db.py`/`verify.py` 路径常量与全部文档/门禁命令
7. **VSCode(pyright) 诊断清零 + 副歌口径复核（2026-09-13）**：修 `api_client`/`extractor`/`sync` 三处告警
   （`sync.pending_downloads` 另含一处真实 bug，见会话日志），并以实时 API 复核副歌 270/204 口径

## 遗留任务（可选，未排期；详见 `docs/API_REFACTOR_PLAN.md` §7「P3 — 展望」）
- 🧑‍⚖️ **需用户决策**：`#349` 的 `Hymn_Downloads/_archive/`（5 个旧资源 + README + md5）去留
- 🔧 可选增强：`tool/bench_api.py` 基准回归脚本、`selenium_legacy` 的 `-m selenium` 用例（当前仅标记注册、无用例）
- 🔧 可选同步：`merged_all.json` 的 category 数据落库核对（`hymn_category` 现由 API 重建为 **47 类**）；
  导出脚本（`tool/merge_to_json.py` / `json_to_db.py`）尚未纳入 `chorus` / `api_raw` 字段
- 🧑‍⚖️ **待确认**：歌词「缺副歌」反馈 —— 已证库内与实时官网 1:1 一致（270 有 / 204 无），
  若要为 204 首补副歌需另开「PDF/OCR 提取」路线（现 OCR 工具在 `tool/`）
- 可能的 UI / 前端展示层（尚未开始）

## 环境与命令
- Python 环境：`/home/zjx/python_env/bin/python`（opencc / rapidocr 等依赖）
- 主依赖：`pip install -r config/requirements.txt`；保底引擎：`pip install -r config/requirements-selenium.txt`（+ Chrome/chromedriver）
- 运行：`python crawler_api.py`（交互）/ `python crawler_api.py --engine api --step 1`（非交互）；保底：`python legacy/crawler_selenium.py`
- 校验：`python crawler_api.py --step check`（三方一致）、`python -m crawler_core.verify`（全量对账 + `data/final_report.txt`）
- 歌词复核：`python tool/show_lyrics.py 12` / `--stats` / `--no-chorus`（看正歌+副歌并与 `api_raw` 逐字比对）
- 测试与门禁：`python -m pytest -c config/pytest.ini test/ -q`、`ruff check --config config/ruff.toml .`、
  `mypy crawler_core crawler_api.py legacy/crawler_selenium.py`、`bandit -c config/bandit.yaml -r crawler_core crawler_api.py legacy/crawler_selenium.py`
- 详细方案：`docs/API_REFACTOR_PLAN.md`；上下文最小化：`docs/CLINE_CONTEXT_MINIMIZE.md`；会话日志：`docs/sessions/`
