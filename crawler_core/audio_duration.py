# crawler_core/audio_duration.py
# 音频时长读取（v10 `tjc_hymn.audio_durations` 的数据来源）
#
# 背景（2026-09-19）：
#   库内原本只有音频**路径**（`audio_versions`）与**版本名列表**（`audio_version_list`），
#   没有任何可展示/可排序的时长信息。`audio_durations` 以 `{版本名: 秒}` 与之**键集一一匹配**，
#   由 `tool/build_audio_durations.py` 离线读取本地音频文件（`Hymn_Downloads/`）填充。
#
# 为什么按扩展名显式选 mutagen 子类（实测结论，不能图省事用 mutagen.File）：
#   `mutagen.File()` 靠**类型嗅探**（ID3 头 / 后缀 / 评分）选实现，对本库 3 个**无 ID3 标签**的
#   MP3（#138 / #197 / #296_b 人聲版）返回 None；而显式 `mutagen.mp3.MP3(path)` 能正常读出
#   （89.05 / 185.04 / 252.43 秒）。故按后缀分派：.mp3 → MP3、.m4a/.mp4/.m4b → MP4，
#   未知后缀才回落 `mutagen.File`。全库 1119 个音频实测 **1119 读出、0 失败**。
#
# 依赖：mutagen（见 `config/requirements.txt`）；缺失时抛 RuntimeError 并给出安装命令。

import json
import os
import sqlite3
from typing import Any, TypedDict

from .config import SCRIPT_DIR
from .naming import is_audio_version_key

# 后缀 → mutagen 子类（显式分派，避免类型嗅探失败）
MP4_EXTS = (".m4a", ".mp4", ".m4b", ".aac")
MP3_EXTS = (".mp3",)
KNOWN_AUDIO_EXTS = MP4_EXTS + MP3_EXTS


def resolve_audio_path(rel_path: str | None, root: str | None = None) -> str:
    """DB 内音频路径 → 本地绝对路径

    库内 `audio_versions` 存的是**相对项目根**的路径（如 `Hymn_Downloads/001_x/1_鋼琴版.m4a`），
    但也可能收到绝对路径（测试/外部数据）→ 绝对路径原样返回；空值返回空串。
    """
    if not rel_path:
        return ""
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(root or SCRIPT_DIR, rel_path)


def _reader(ext: str) -> Any:
    """按后缀取 mutagen 读取器（惰性 import；缺依赖给出可操作提示）"""
    try:
        if ext in MP4_EXTS:
            from mutagen.mp4 import MP4

            return MP4
        if ext in MP3_EXTS:
            from mutagen.mp3 import MP3

            return MP3
        from mutagen import File as MutagenFile  # 未知后缀：兜底嗅探

        return MutagenFile
    except ImportError as exc:  # pragma: no cover - 仅在依赖缺失时触发
        raise RuntimeError(
            "缺少依赖 mutagen，请执行：pip install -r config/requirements.txt"
        ) from exc


def read_duration_ex(path: str | None) -> tuple[float | None, str]:
    """读单个音频文件的时长 → `(秒, 备注)`

    失败时秒为 `None`、备注写明原因（文件缺失 / 解析异常 / 时长为 0）；
    单个文件损坏**不抛异常**——批量统计时不应被一个坏文件打断。
    """
    if not path:
        return None, "未提供路径"
    if not os.path.isfile(path):
        return None, f"文件不存在：{path}"
    ext = os.path.splitext(path)[1].lower()
    reader = _reader(ext)
    try:
        audio = reader(path)
        seconds = float(audio.info.length) if audio is not None and audio.info else 0.0
    except Exception as exc:  # noqa: BLE001 - 坏文件只影响自身，不影响批量
        return None, f"{type(exc).__name__}: {exc}"
    if seconds <= 0:
        return None, "读到时长 0（文件可能损坏）"
    return seconds, ""


def read_duration(path: str | None) -> float | None:
    """读单个音频文件的时长（秒）；失败返回 None（原因见 `read_duration_ex`）"""
    return read_duration_ex(path)[0]


def durations_for(versions: dict[str, str], root: str | None = None
                  ) -> tuple[dict[str, float | None], dict[str, str]]:
    """`{版本名: 相对路径}` → `({版本名: 秒或 None}, {版本名: 失败原因})`

    键集 = 入参中的**真实版本键**（`_` 前缀的元信息键 `_error`/`_url`/`_http_status` 等按
    `naming.is_audio_version_key` 剔除）——这正是「与 `audio_versions` / `audio_version_list`
    一一匹配」的落实点：个别文件读不出时**键仍保留、值落 `None`**，键集不随之塌缩。

    秒保留 **3 位小数**（与写库格式一致，避免 JSON 里出现 144.11754166666665 这种长尾）。
    """
    durations: dict[str, float | None] = {}
    issues: dict[str, str] = {}
    for ver, rel in versions.items():
        if not is_audio_version_key(ver):
            continue
        seconds, reason = read_duration_ex(resolve_audio_path(rel if isinstance(rel, str) else "",
                                                             root))
        if seconds is None:
            durations[ver] = None
            issues[ver] = reason
        else:
            durations[ver] = round(seconds, 3)
    return durations, issues


# ================= 批量统计与入库（全链路与 tool 共用） =================
#
# 为什么做成共用函数（2026-09-19）：时长既要**随下载一起入库**（`downloader._backfill_paths_to_db`
# 回写路径时顺手算），又要能**独立重算/补算**（`crawler_api.py --step 11`、`tool/build_audio_durations.py`），
# 三处必须是同一套键集规则，否则「一一匹配」会在某个入口破功。

class DurationFillSummary(TypedDict):
    """`fill_durations()` 的汇总（多形状：计数 + 不一致明细 + 问题明细）

    ⚠️ 必须显式声明（项目规则）：从字面量推断会收窄成 `dict[str, int | list[...]]`，
    调用方 `summary["written"]` / `summary["issues"]` 会被误报。
    """

    hymns: int                                    # 处理后（有时长键）的诗歌数
    entries: int                                  # 音频条目数（= 时长键数）
    read: int                                     # 读出秒数的条目
    null: int                                     # 读不出（值 None）的条目
    written: int                                  # 实际写入/更新的行数
    unchanged: int                                # JSON 未变、跳过的行数
    rows: int                                     # 库内总行数
    selected: int                                 # 本次处理的行数（--only/--limit 后）
    mismatch: list[tuple[str, list[str], list[str]]]  # [(编号, 时长键, audio_version_list)]
    issues: list[tuple[str, str, str]]            # [(编号, 版本, 失败原因)]


def load_version_rows(db_path: str | None = None) -> list[tuple]:
    """读 `tjc_hymn` 音频四列 → `[(编号, audio_versions, audio_version_list, audio_durations)]`

    先幂等补 v10 列（兼容未迁移的旧库）；按 `id` 排序（编号含 `296_b` 等，不能按编号排）。
    """
    from . import db  # 惰性导入：本模块只读文件，避免与 db 的常规导入顺序耦合

    conn = sqlite3.connect(db_path or db.DB_PATH)
    try:
        c = conn.cursor()
        db.ensure_audio_durations_field(c)
        return c.execute(
            "SELECT hymn_number, audio_versions, audio_version_list, audio_durations "
            "FROM tjc_hymn ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def durations_of(audio_versions_json: str | None, root: str | None = None
                 ) -> tuple[dict[str, float | None], dict[str, str]]:
    """`audio_versions` JSON → `({版本名: 秒或 None}, {版本名: 失败原因})`（脏数据 → 空）"""
    try:
        av = json.loads(audio_versions_json or "{}")
    except (json.JSONDecodeError, TypeError):  # 历史脏数据：视为无音频
        av = {}
    return durations_for(av if isinstance(av, dict) else {}, root)


def _version_set(value: str | None) -> set[str]:
    """`audio_version_list` JSON → 真实版本名集合（元信息键 / 脏数据剔除）"""
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {k for k in parsed if isinstance(k, str) and is_audio_version_key(k)}


def fill_durations(db_path: str | None = None, numbers=None, limit: int | None = None,
                   dry_run: bool = False, root: str | None = None, on_row=None
                   ) -> DurationFillSummary:
    """批量统计各版本时长并写 `tjc_hymn.audio_durations`（v10）→ 汇总

    Args:
        db_path: 数据库路径（默认 `config.DB_PATH`）
        numbers: 只处理这些编号（None / 空 = 全部）
        limit: 最多处理多少首
        dry_run: 只统计不写库
        root: 相对路径解析根（默认项目根；测试可注入临时目录）
        on_row: 逐首回调 `(i, total, 编号, {版本: 秒或 None})`（用于 CLI 明细输出）

    幂等：JSON 未变的行只计入 `unchanged`、不发起 UPDATE。
    """
    from . import db  # 惰性导入（见 load_version_rows 注释）

    rows = load_version_rows(db_path)
    total = len(rows)
    if numbers:
        wanted = {str(n) for n in numbers}
        rows = [r for r in rows if r[0] in wanted]
    if limit:
        rows = rows[:limit]

    summary: DurationFillSummary = {"hymns": 0, "entries": 0, "read": 0, "null": 0,
                                    "written": 0, "unchanged": 0,
                                    "rows": total, "selected": len(rows),
                                    "mismatch": [], "issues": []}
    for i, (no, av_json, vl_json, old_json) in enumerate(rows, 1):
        durations, issues = durations_of(av_json, root)
        if not durations:
            continue  # 无音频的记录（源站空白条目）直接跳过
        keys = set(durations)
        listed = _version_set(vl_json)
        if keys != listed:
            summary["mismatch"].append((no, sorted(keys), sorted(listed)))
        summary["hymns"] += 1
        summary["entries"] += len(durations)
        summary["read"] += sum(1 for s in durations.values() if s is not None)
        summary["null"] += sum(1 for s in durations.values() if s is None)
        summary["issues"] += [(no, ver, reason) for ver, reason in issues.items()]
        if on_row is not None:
            on_row(i, len(rows), no, durations)

        payload = json.dumps(durations, ensure_ascii=False)
        if payload == (old_json or ""):
            summary["unchanged"] += 1
            continue
        summary["written"] += 1
        if not dry_run:
            db.save_audio_durations(no, durations, db_path)
    return summary

