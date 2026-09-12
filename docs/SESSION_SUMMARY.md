# hymn_crawler 开发总结（SESSION_SUMMARY）

> 用途：新会话续接开发的最小上下文入口。更新于 2026-09-12。

## 项目状态
- TJC 赞美诗（hymn）数据爬虫 + 数据处理流水线，已完成：探测 → 下载 → 提取 → OCR → 合并 → 入库全流程
- 核心模块：`crawler_core/`（爬虫框架）、`tool/`（数据处理工具）、`test/`（测试）
- 数据文件：`tjc_hymn.db`（SQLite v6，474 首，含副歌 `chorus`）、`probe_report.json`、`final_report.txt`、`merged_all.json`
- 歌词以官网 JSON API（`/api/hymn/{no}`）为权威源：正歌 → `verse_1..10`，副歌 → `chorus`
- 最新提交 a82e40d：文档防污染规则 + .clinerules 清理

## 最近主要工作
1. 全流程流水线打通（选项7 全自动，已有内容自动跳过）
2. 增量补探缺失音频（458/474 有音频）
3. pre-commit 钩子修复（移除长耗时全量哈希重建，避免 Cline 中断）
4. 测试迁移至 `test/` 目录并修正路径（ruff 0 / bandit 0 / mypy 0 / pytest 通过）
5. 修复歌词采集缺陷：旧版每个 Tab 只取首个 `.lyrics_box`，导致 270 首副歌整段丢失；
   新增 `crawler_core/lyrics_api.py`（官网 API 取词）+ DB v6 `chorus` 字段，全量重抓 474 首

## 遗留任务
- 确认 merged_all.json 的 category 数据在 DB 中完整落库
- 可能的 UI / 前端展示层（尚未开始）
- 长期数据校验与增量更新策略（歌词可用 `lyrics_api.run()` 增量刷新）

## 环境与命令
- Python 环境：`/home/zjx/python_env/bin/python`（opencc / rapidocr 等依赖）
- 测试：`/home/zjx/python_env/bin/python -m pytest test/ -x -q`
- 详细方案：`hymn_crawler_plan.md`；上下文最小化：`docs/CLINE_CONTEXT_MINIMIZE.md`