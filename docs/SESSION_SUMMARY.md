# hymn_crawler 开发总结（SESSION_SUMMARY）

> 用途：新会话续接开发的最小上下文入口。更新于 2026-09-13（API 重构 P0/P1/P2 完成）。

## 项目状态
- TJC 赞美诗（hymn）数据爬虫 + 数据处理流水线，已完成：探测 → 下载 → 提取 → 转图 → 校验 → 入库全流程
- **默认引擎 = 官网 JSON API**（纯 `requests`，零浏览器依赖）：`crawler_fast.py`（`--engine api|selenium|auto`）
- **Selenium 保底引擎**完整保留：`crawler_core/selenium_legacy/` + 独立整链入口 `crawler_selenium.py`
- 核心模块：`crawler_core/`（`api_client` / `naming` / `scanner` / `extractor` / `probe` / `sync` / `downloader` /
  `verify` / `db` + `selenium_legacy/`）、`tool/`（数据处理 + `tool/data_audit/` 审计）、`test/`（81 项 pytest）
- 数据文件：`tjc_hymn.db`（SQLite **v7**，474 首，含副歌 `chorus` 与 API 原始记录 `api_raw`）、`probe_report.json`、
  `final_report.txt`、`Hymn_Downloads/api_cache/`（48 页 API 缓存，已入 git）
- 数据口径：三方对账 **474/474/474**；资源 **2067/2067 完整（100%）**；音频可用 **1119** 条 / 474 首；
  源站不可用 **10** 条（9 条空记录 + #62 404，已摘出期望集合）；`download_status` 全量 `completed`
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

## 遗留任务
- `merged_all.json` 的 category 数据落库核对（`hymn_category` 现由 API 重建为 **47 类**）
- 可选优化：`tool/bench_api.py` 基准回归脚本、`selenium_legacy` 的 `-m selenium` 用例、`#349` 留档目录去留
- 可能的 UI / 前端展示层（尚未开始）

## 环境与命令
- Python 环境：`/home/zjx/python_env/bin/python`（opencc / rapidocr 等依赖）
- 主依赖：`pip install -r requirements.txt`；保底引擎：`pip install -r requirements-selenium.txt`（+ Chrome/chromedriver）
- 运行：`python crawler_fast.py`（交互）/ `python crawler_fast.py --engine api --step 1`（非交互）
- 校验：`python crawler_fast.py --step check`（三方一致）、`python -m crawler_core.verify`（全量对账 + `final_report.txt`）
- 测试与门禁：`python -m pytest test/ -q`、`ruff check`、`mypy crawler_core`、`bandit -c bandit.yaml -r crawler_core`
- 详细方案：`docs/API_REFACTOR_PLAN.md`；上下文最小化：`docs/CLINE_CONTEXT_MINIMIZE.md`；会话日志：`docs/sessions/`
