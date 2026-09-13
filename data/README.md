# `data/` — 流水线数据产物

> 2026-09-13 目录重排：为让项目根目录只保留 `README.md` / `crawler_api.py` / `tjc_hymn.db`，
> 原根目录的两份产物集中到此。**路径由 `crawler_core/config.py` 统一定义**：
> `DATA_DIR` / `PROBE_REPORT` / `FINAL_REPORT`（代码里请引用常量，不要硬编码字符串）。

| 文件 | 生成者 | 内容 | 是否入 git |
| --- | --- | --- | --- |
| `probe_report.json` | `crawler_core/probe.py`（Step 3 资源探测） | 474 首资源清单：五线谱/简谱 PDF URL、各音频版本 URL、`_http_status` / `_unavailable` / `_site_removed` 元信息（`downloader` / `verify` / `sync` 均消费它） | ✅ 跟踪（作为 DB 重建的保险） |
| `final_report.txt` | `crawler_core/verify.py`（Step 5 校验） | 三方对账 + 资源核验 + 失败任务归档的最终统计报告 | ✅ 跟踪 |

- 本目录下的 `.json` / `.txt` 均为**产物**，可由流水线重新生成；但请勿随手删除 `probe_report.json`
  （`downloader` 依赖它决定下载清单；缺失会被视为「尚未探测」）。
- DB 与下载资源仍在项目根 / `Hymn_Downloads/`：`tjc_hymn.db`、`Hymn_Downloads/`。
