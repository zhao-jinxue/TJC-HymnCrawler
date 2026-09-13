# crawler_core/naming.py
# 命名真源（v1）：目录名 / 资源文件名 / 音频版本名
#
# 为什么单独成模块（§4.4「命名一致性」）：
#   历史上 Selenium 版与「DOM 分类名」驱动命名各存一份，站点把分类由「合唱-N部」
#   改名为「四部合唱-N部」后产生 136 个重复文件（134.7 MB，已于 2026-09-12 清理）。
#   现在 API 引擎与 Selenium 保底引擎**都必须**调用本模块，杜绝规则再次漂移。
#
# 规则（与既有 474 个目录 / 文件名完全一致，实测 474/474 命中）：
#   目录名   : f"{seq:03d}_{sanitize(hymn_number + title)}"   # seq = 列表位置(1..474)
#   乐谱 PDF : f"{hymn_number}_五线谱.pdf" / f"{hymn_number}_简谱.pdf"
#   音频     : f"{hymn_number}_{版本名}版.{归一化后缀}"        # 版本名 = API audio_category.name
#
# ⚠️ 不得混用 seq 与 hymn_number：两者 423/474 不相等（存在 51_a/51_b 等 5 对互见编号），
#    目录名只用 seq，资源文件名只用 hymn_number。

import os

# 音频扩展名归一化（§5.9.4）：官网个别音频以 MP4 容器提供（如 #201 人聲版，实测 video/mp4）
# 同容器改名不影响播放/转换链路，但可避免下游 RESOURCE_EXTS 统计漏项
AUDIO_EXT_ALIASES = {
    "mp4": "m4a",
    "m4a": "m4a",
    "mp3": "mp3",
    "m4b": "m4a",
    "aac": "m4a",
    "wav": "m4a",
}

# 乐谱类型 → 文件名中的中文标签（保持历史命名）
PDF_KIND_LABELS = {
    "staff": "五线谱",
    "numbered": "简谱",
}

# 资源文件名与 DB 相对路径的目录前缀
SAVE_DIR_NAME = "Hymn_Downloads"


def sanitize(text):
    """目录名安全化：仅保留字母/数字（含 CJK）与空格、下划线、连字符

    与历史 `Scanner._create_dir` 的实现逐字符等价——不得改动，否则 474 个既有目录会失配。
    """
    safe = "".join(c for c in (text or "") if c.isalnum() or c in " _-").strip()
    return safe or "Unknown"


def to_dirname(seq, hymn_number, title):
    """目录名：`{seq:03d}_{sanitize(no + name)}`（历史格式，保证零迁移）"""
    return f"{int(seq):03d}_{sanitize(f'{hymn_number}{title}')}"


def seq_from_index(index):
    """列表位置（1-based 下标）→ 3 位序号字符串"""
    return f"{int(index):03d}"


def pdf_name(hymn_number, kind):
    """乐谱 PDF 文件名：kind ∈ {staff, numbered}（亦接受「五线谱」/「简谱」）"""
    label = PDF_KIND_LABELS.get(kind, kind)
    return f"{hymn_number}_{label}.pdf"


def audio_version_name(category_name):
    """API 音频分类名 → 本地版本名（`鋼琴` → `鋼琴版`；已带 `版` 则不重复添加）"""
    name = (category_name if isinstance(category_name, str) else str(category_name or "")).strip()
    name = name or "未知"
    return name if name.endswith("版") else f"{name}版"


def normalize_audio_ext(raw_ext):
    """音频后缀归一化：`.mp4` → `.m4a`（返回不带点的规范后缀，默认 `m4a`）"""
    ext = (raw_ext or "").strip().lstrip(".").lower()
    return AUDIO_EXT_ALIASES.get(ext, ext or "m4a")


def audio_name(hymn_number, version, ext):
    """音频文件名：`{no}_{版本名}.{ext}`（版本名自动补 `版`，ext 自动归一化）"""
    ver = audio_version_name(version)
    return f"{hymn_number}_{ver}.{normalize_audio_ext(ext)}"


def rel_path(dir_name, filename):
    """资源文件的 DB 相对路径（相对于项目根，与 downloader 的写法一致）"""
    return os.path.join(SAVE_DIR_NAME, dir_name, filename)


def is_audio_version_key(key):
    """音频版本键是否为真实资源（`_` 前缀为元信息键：_error/_url/_http_status/_unavailable）"""
    return bool(key) and not str(key).startswith("_")
