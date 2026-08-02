# 会话状态汇总（用于上下文压缩后快速恢复 / 作为第四阶段新会话的启动文档）

> ⚠️ **重要**：本文件已同步到 **v4 实际状态**（2025 实时数据）。此前版本（v3 / 音频 99.6% / audio_versions 存 URL 对象）已过时，请以本版本为准。

## 项目
真耶穌教會聖樂网爬虫 — 抓取 **474 首**诗歌（sacredmusic.tjc.org.tw）

## 当前阶段
**第四阶段（项目收尾与数据校验）已完成 ✅**，项目已验收归档。
产出：`step4_verify_and_report.py` + `final_report.txt`。

## 核心架构

```
hymn_crawler/
├── crawler_core/                  # 主代码包（7 模块）
│   ├── __init__.py
│   ├── config.py                  # 全局 URL / 路径配置
│   ├── driver.py                  # Selenium 驱动（极致加速配置）
│   ├── db.py                      # 数据库管理 + 四段式(v1→v4)迁移
│   ├── scanner.py                 # Step 1: 扫描列表页创建目录
│   ├── extractor.py               # Step 2: 提取详情文本入库
│   ├── probe.py                   # 资源探测（PDF HEAD + 音频页面点击）
│   └── downloader.py              # 资源下载管理器（含下载/完整性校验）
├── crawler_fast.py                # 统一菜单入口（主入口，含 print_db_status）
├── step4_verify_and_report.py     # 第四阶段：校验与报告（数据对账/资源核验/final_report）
├── tjc_hymn.db                    # SQLite 数据库（v4 结构）
├── probe_report.json              # 资源探测清单 + 各状态（474 首）
├── final_report.txt               # 项目最终执行统计报告（第四阶段产物）
├── Hymn_Downloads/                # 474 首诗歌本地目录
├── hymn_crawler_plan.md           # 开发计划文档
├── SESSION_SUMMARY.md             # 本文件（会话恢复/新会话启动文档）
└── (遗留) step1/step2/test_step*.py / old_v1_v2_v3脚本
```

## 数据库（tjc_hymn.db）— 当前 V4 结构

**474/474 首**全部入库：
- 歌词：**474/474**（100%）
- 五线谱 `staff_img_path`：**474/474**（100%）
- 简谱 `numbered_img_path`：**474/474**（100%）
- 有音频（`audio_version_list` 非空）：**474/474**（100%）

### 关键字段（均已实际落库）
| 字段 | 类型/内容 | 说明 |
| :--- | :--- | :--- |
| `hymn_number` | TEXT | 诗歌编号（UNIQUE）|
| `title` / `lyricist` / `composer` / `source_info` | TEXT | 文本信息 |
| `verse_count` + `verse_1`~`verse_10` | INT/TEXT | 歌词内容 |
| `staff_img_path` | TEXT | **五线谱相对路径**（相对项目根目录）|
| `numbered_img_path` | TEXT | **简谱相对路径**（相对项目根目录）|
| `audio_versions` | TEXT(JSON) | **版本名 → 本地相对路径字符串**（见下方格式）|
| `audio_version_list` | TEXT(JSON) | 纯版本名列表，如 `["鋼琴版","人聲版"]` |
| `download_status` | TEXT | `completed`/`partial(x/y)`/`failed`/`pending`/`dir_missing`/`no_files` |
| `integrity_status` | TEXT | `passed`/`failed`/`unchecked`（文件完整性校验结果）|
| `updated_at` | TIMESTAMP | `datetime('now','localtime')` 本地时间 |

### ⚠️ audio_versions 存储格式（已变更！）
**当前存的不是线上 URL 对象，而是【版本名 → 本地相对路径字符串】：**
```json
{
  "鋼琴版": "Hymn_Downloads/002_2讚美聖父/2_鋼琴版.m4a",
  "人聲版": "Hymn_Downloads/002_2讚美聖父/2_人聲版.mp3"
}
```
> ⚠️ 新会话注意：所有路径（staff/numbered/audio）都是**相对项目根目录**。若需绝对路径，用 `os.path.join(SCRIPT_DIR, relpath)`，其中 `SCRIPT_DIR` 可由 `os.path.dirname(os.path.abspath(__file__))` 计算。
>
> ⚠️ 注意区分：`probe_report.json` 里的 `audio_versions` **仍是线上的 URL 对象格式**（`{"url":...,"filename":...,"ext":...}`），只有**数据库**里是相对路径字符串。二者不要混淆。

### 数据库迁移（db.py 的 init_db() 自动执行）
- 检测字段组合自动迁移，幂等，最终到 v4
- v1（piano_audio_path+vocal_audio_path）→ v4
- v2（有 audio_versions 缺 audio_version_list）→ v4
- v3（缺 download_status/integrity_status）→ v4（补字段）
- v3/v4 还会从 `probe_report.json` 回填空音频记录（`_backfill_from_probe`）

## 资源探测结果（probe_report.json）
- PDF 五线谱 / 简谱：**474/474** ✅
- 音频：**474/474** ✅（原先 #410/#459 缺音频、6 首 `_error` 均已重新探测修复）
- 版本分布（已过滤 `_error`）：
  | 版本 | 数量 |
  | :--- | :---: |
  | 鋼琴版 | 473 |
  | 人聲版 | 462 |
  | 四部合唱版 | 47 |
  | 合唱-1/2/3/4部版 | 各 35 |

## 下载结果（download_status / integrity_status）
- download_status：`completed` 473，`partial(3/4)` 1（#62）
- integrity_status：`passed` 473，`failed` 1（#62，人聲版文件损坏/缺失）
- **#62 人聲版**：服务器端该文件 404（网页有按钮但文件缺失），是"服务器端事实"，非代码 bug。第四阶段需将其标记归档。

## 可直接调用的关键函数（第四阶段复用，避免重复造轮子）

### crawler_core/db.py
```python
from crawler_core.db import (
    init_db,               # 初始化/迁移到v4（主入口启动时已调用）
    print_db_status,       # 打印数据库统计
    print_url_map_status,  # 打印 url_map.txt 统计
    count_failed,          # 统计 verse_count=0 的提取失败数
    get_failed_songs,      # 获取提取失败歌曲列表
    sync_download_status_to_db,  # 将 probe_report 的 download_status 同步到库
    update_integrity_status,     # 单首更新 integrity_status（同步库+probe_report.json）
    batch_update_integrity,      # 批量更新 integrity_status
    save_to_db,            # UPSERT 单首数据
)
```

### crawler_core/downloader.py
```python
from crawler_core.downloader import (
    run_download,          # 主下载入口（会自动校验+同步状态+回写路径）
    verify_file_integrity, # 单文件完整性校验（PDF/M4A/MP3 头部检查）
    _backfill_paths_to_db, # 将 PDF+audio 相对路径回写数据库（download 后自动调用）
)
```
> `_backfill_paths_to_db` 是最新核心函数：把 `staff_img_path`/`numbered_img_path`/`audio_versions`(相对路径) + `audio_version_list` 一次性回写数据库。

### crawler_core/probe.py
```python
from crawler_core.probe import (
    run_probe,          # 资源探测（PDF HEAD + 音频页面点击）
    load_probe_report,  # 读取 probe_report.json
)
```

### crawler_fast.py 菜单（主入口）
- 选项 1-7：Step1 / Step2 / 资源探测 / 下载 / **校验与报告(step4)** / 全流程(Step1→Step2→探测→下载→校验) / 补全失败(仅当存在失败时显示)
- 启动时自动：`init_db()` + `print_db_status()` + `print_url_map_status()` + `print_probe_report_status()`
- `print_probe_report_status()` 已过滤 `_error` 版本、显示 download/integrity 分布

## 第四阶段（已完成 ✅ 2026-08-02）
### 🔴 核心交付（全部完成）
- [x] 编写 `step4_verify_and_report.py`
- [x] 数据完整性校验：DB(474) vs 目录(474) vs url_map(474) 三方完全一致，双向零差异
- [x] 多媒体资源对账（按版本聚合）：五线谱/简谱/鋼琴版 100% 完整；人聲版 461/462（#62 服务器 404 缺失）；合唱部版 140、四部合唱版 47 均 100%
- [x] 失败任务归档：#62 人聲版为服务器端 404，已标记归档、不重试
- [x] DB 路径交叉校验：无悬挂引用（DB 全部相对路径命中磁盘文件）
- [x] 生成 `final_report.txt`（资源总数 2070，完整 2069，99%）
- [x] 项目验收与归档；step4 集成进 crawler_fast.py 菜单（选项 5=独立校验，选项 6=全流程末尾校验）

> 📌 final_report 关键结论：`资源总数 2070 / 完整 2069 (99%)`，唯一缺失 #62 人聲版（服务器端 404 事实）。

### 🟡 计划文档遗留的优化项
- [x] ~~目录命名清理~~：实测 474 目录**无全角空格/半角空格**，无需处理
- [x] **Step 2 速度优化**：已用精确等待替代固定 `sleep(0.8)+sleep(0.5)` 与 Tab 点击后 `sleep(0.2)`（extractor.py)。仅保留源考折叠面板展开的 `sleep(0.3)`（UI 动画）。实测 3 首 8.5s、5 首 12s（约 0.4s/首 提速），预估较原 ~3s/首 快约 5 倍
- [x] **Step 2 断点续爬**：extractor.py 新增 progress 文件机制（`Hymn_Downloads/step2_progress.json`，每成功 1 首立即持久化；`resume=False`/`clear_progress()` 可全量重跑）。已实测：5 首成功建立进度，二次运行 5 首全部跳过、耗时 0.0s
- [x] ~~内存分批入库（每 50 首 flush）~~：已决定不做（474 首内存约数 MB，无必要）

### ⚠️ url_map.txt 追踪状态（已解决 2026-08-02）
- `url_map.txt` 现**已在 git 跟踪内**：`.gitignore` 增加白名单 `!/Hymn_Downloads/url_map.txt` 并已 `git add`。
- 应急重建命令仍保留：`python3 step4_verify_and_report.py --rebuild-map`（从 DB staff_img_path 重建）。

## ⚠️ 已修复的 bug（2026-08-02 全量执行 Step 2 时发现）
- **`save_to_db` UPSERT 覆盖资源路径/状态**：Step 2 全量重跑时，`_parse_one` 返回的空 `staff_img_path`/`numbered_img_path`/`audio_versions` 与默认 `pending`/`unchecked` 经 UPSERT 覆盖了第三阶段已回写的路径和 completed/passed 状态（一度 0/474）。
- **修复**：`save_to_db` 的 ON CONFLICT 对路径字段用 `CASE ... 新值为空则保留旧值`，对状态字段在 `('', 'pending')`/`('', 'unchecked')` 时保留旧值。已修复验证通过（模拟 Step 2 空数据 UPSERT 不再清空 #1 路径/状态）。
- **恢复**：全量重跑后曾用 `_backfill_paths_to_db` + `sync_download_status_to_db` + `batch_update_integrity` 从磁盘/probe_report.json 恢复，最终全量复验 **474/474 健康**（staff/num/audio 路径 + 状态全部对照 probe 正确）。
- **教训**：Step 2 不能直接对已有资源数据的库全量重跑（现已安全，但重跑前建议先确认 save_to_db 已是修复版）。

## 关键决策记录
1. 音频 URL 用 MD5 哈希，无法拼接，必须 Selenium 页面点击捕获 `audio#player.src`
2. `audio_version_list` 独立存版本名，避免每次解析大 JSON
3. crawler_fast.py 统一菜单 + 启动自动打印数据库/探测状态
4. 数据库四段式自动迁移（v1→v4），幂等
5. `download_status` 追踪每首下载状态；`integrity_status` 追踪文件完整性（passed/failed/unchecked）
6. `audio_versions` 在**数据库**中改为相对路径字符串（v4 变更），`probe_report.json` 仍为 URL 对象
7. 所有路径相对项目根目录（SCRIPT_DIR），跨机器可移植
8. #62 人聲版为服务器端 404（网页有按钮但文件缺失），非代码 bug，需标记归档
9. **全流程（菜单6）重跑原则（2026-08-02 确认）**：重跑以**刷新状态字段**（download_status/integrity_status）为主，不涉及其他字段（路径/音频版本等保持不变）；**仅当源站有更新**（如探测发现新音频版本）时才更新其他字段。当前实现已符合：`save_to_db` CASE 保护路径/状态不覆盖；`run_download` 已存在文件跳过、仅更新状态并校验。
