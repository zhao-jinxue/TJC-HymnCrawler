# `tool/data_audit/` — 数据审计与取证脚本

> 来源：2026-09-12「API 重构」会话中的数据先行对账工作（原脚本位于 `/tmp`，按方案 §8 收编入库）。
> 用途：**站点改版 / 数据异常后一键复检**。全部为**只读取证**（唯 `apply_cleanup.py` 会改数据，且内置 md5 守卫与 `--dry-run`）。

| 脚本 | 用途 | 安全性 |
| --- | --- | --- |
| `check_dup2.py` | 全量重复文件对账：统计 API 分类名分布、对 34 首 × 4 部文件做全文件 md5、输出本地「同内容不同名」配对清单 | 只读 |
| `verify_before_delete.py` | 删除前核验：被删文件是否被 `probe_report.json` / DB `audio_versions` / `checksums.json` 引用；#201 本地与远端 md5 比对 | 只读 |
| `reconcile_final.py` | 终版音频对账：期望（API 实测可用 1119）vs 本地文件，输出缺失 / 多余清单（含 `.mp4` 归一化盲点修正） | 只读 |
| `apply_cleanup.py` | 数据清理执行：删除重复文件（逐对 `assert md5` 守卫）+ `.mp4 → .m4a` 归一化 + `#349` 目录迁移 | **会改数据**（建议先 `--dry-run`） |

## 复检建议顺序

```bash
P=/home/zjx/python_env/bin/python
$P tool/data_audit/reconcile_final.py      # 1) 音频口径是否仍「缺失 0」
$P tool/data_audit/check_dup2.py           # 2) 是否又出现同内容重复文件（分类改名类问题）
$P crawler_fast.py --step check            # 3) API 列表 vs url_map vs 本地目录 三方一致
$P crawler_core/verify.py                  # 4) 全量校验 + final_report.txt
```

> ⚠️ 这些脚本内含本机绝对路径（`/home/zjx/hymn_crawler`），迁移机器时请先替换路径常量。
> 📌 `apply_cleanup.py` 的操作在 2026-09-12 已执行完毕（删除 136 个重复文件 / `.mp4→.m4a` / `#349` 目录迁移），
> 再次运行前请先确认「官网分类分布」与「本地文件清单」确实出现新的偏差。
