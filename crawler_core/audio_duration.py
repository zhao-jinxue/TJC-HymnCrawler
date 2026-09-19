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

import os
from typing import Any

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
            durations[ver] = seconds
    return durations, issues
