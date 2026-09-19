# crawler_core/jianpu_sync.py
# 简谱数据（v8 PPT 带简谱歌词 + v9 官方简谱曲谱）的**增量同步编排**（全链路与 tool 共用）
#
# 为什么需要（2026-09-19）：
#   两套简谱数据的抽取/入库此前只有独立工具（`tool/extract_jianpu.py`、`tool/build_score.py`），
#   爬虫全流程（`crawler_api.py`）跑完新下载的诗歌后**不会**自动生成曲谱/歌词对位 →
#   库里会出现「有 `{编号}_简谱.pdf` 却无 `hymn_score*`」的空档（每新增一首都要人工记得跑工具）。
#   本模块把「哪些该算 + 算完怎么入库」收敛成两个函数，crawler_api（菜单 12/13、`--step 12|13`）
#   与两个 tool 共用同一实现，避免判据漂移。
#
# 增量判据（幂等，可反复跑）：
#   - **v9 曲谱**：有官方简谱 PDF 的编号 − `hymn_score` 已入库的编号（已入库但 `pdf_path` 变了也算待处理）；
#     `force=True` 全量重算。**注意 `#349`**：站点上传的是无文本层异版 PDF，抽取结果无谱行 →
#     `save_score_records` 会跳过、于是每轮都会重新尝试（≈0.1s，报告中显式列出原因），这是已知边界。
#   - **v8 PPT**：`data/赞美诗PPT/*.ppt` 中 `src_md5` 与 `hymn_jianpu` 不同（或未入库）的文件——
#     PPT 是**外部整理素材**（不在爬虫下载链路里，目录缺失时整体跳过）。
#
# 学习（`--learn`）**不在此模块**：码位映射是低频标定动作，仍需人工在
# `tool/build_score.py --learn` 里跑（本模块只用库内 `hymn_codepoint_map` + 人工种子）。

import os
import sqlite3
import time
from hashlib import md5
from typing import Any, TypedDict

from . import config
from . import pdf_jianpu as P
from . import pdf_score as S
from . import ppt_jianpu as J


class ScoreSyncSummary(TypedDict):
    """v9 官方简谱曲谱增量入库的汇总（多形状：计数 + 跳过明细）"""

    pending: int                      # 本次待处理编号数
    processed: int                    # 实际抽取完成的编号数
    hymns: int                        # 入库首数
    lines: int                        # 入库谱行数
    lyrics: int                       # 入库歌词行数
    chars: int                        # 入库逐字数
    align_ok: int                     # 校验通过（等长）的编号数
    skipped: list[tuple[str, str]]    # [(编号, 跳过原因)]
    seconds: float


class JianpuSyncSummary(TypedDict):
    """v8 PPT 带简谱歌词增量入库的汇总"""

    total: int                        # PPT 目录内 `*.ppt` 总数（目录缺失为 0）
    pending: int                      # 本次待处理（未入库或 md5 变化）
    processed: int                    # 实际解析完成的文件数
    hymns: int                        # 入库首数
    lines: int                        # 入库行数
    skipped: list[tuple[str, str]]    # [(ppt_file, 跳过原因)]
    seconds: float


def _num_key(name: str):
    """编号排序键（数字优先按数值，`296_b` 这类排在同号之后）"""
    head = ""
    for ch in str(name):
        if ch.isdigit():
            head += ch
        else:
            break
    return (int(head) if head else 10 ** 6, str(name))


def _md5_file(path: str) -> str:
    """文件 md5（与 `ppt_jianpu._md5` 同口径：整文件分块 md5，仅作来源溯源指纹）"""
    h = md5()  # nosec B324 - 仅作来源溯源指纹，非安全用途
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel_pdf(path: str) -> str:
    """PDF 绝对路径 → 与 `hymn_score.pdf_path` 同口径（相对项目根；与 `pdf_score._rel` 一致）"""
    try:
        return os.path.relpath(path, config.SCRIPT_DIR)
    except ValueError:  # 跨盘符（Windows）时退回绝对路径
        return path


def _record_get(rec: Any, key: str, default: Any = "") -> Any:
    """`ScoreRecord`(dataclass) / dict 统一取值"""
    if isinstance(rec, dict):
        return rec.get(key, default)
    return getattr(rec, key, default)


def _default_db_path() -> str:
    """默认数据库路径（取 `db.DB_PATH`，便于测试 monkeypatch；与项目其它模块一致）"""
    from . import db

    return db.DB_PATH


def _score_done(db_path: str | None) -> dict[str, str]:
    """`hymn_score` 已入库的 {编号: pdf_path}（表不存在 → 空）"""
    conn = sqlite3.connect(db_path or _default_db_path())
    try:
        try:
            return {str(n): (p or "") for n, p in
                    conn.execute("SELECT hymn_number, pdf_path FROM hymn_score")}
        except sqlite3.OperationalError:  # v9 表尚未创建
            return {}
    finally:
        conn.close()


def score_pending_numbers(db_path: str | None = None) -> list[str]:
    """有官方简谱 PDF 但 `hymn_score` 未入库（或 `pdf_path` 已变）的编号（数字序）"""
    done = _score_done(db_path)
    pending = [num for num, path in P._pdf_index().items()
               if done.get(str(num)) != _rel_pdf(path)]
    return sorted(pending, key=_num_key)


def sync_scores(numbers=None, limit=None, dry_run=False, db_path=None, mapping=None,
                force=False, on_rec=None) -> ScoreSyncSummary:
    """官方简谱曲谱（v9）→ `hymn_score*` 入库（默认**增量**；幂等）

    Args:
        numbers: 指定编号（None 且 `force=False` → 只处理 `score_pending_numbers()`）
        limit: 最多处理多少首
        dry_run: 只抽取不写库
        db_path: 数据库路径（默认 `config.DB_PATH`）
        mapping: 码位→记号映射（默认取库内 `hymn_codepoint_map`；人工种子由 `build_score` 兜底）
        force: 忽略已入库状态、全量重算
        on_rec: 逐首回调 `(rec, i, total)`
    """
    from . import db

    if numbers:
        nums = [str(n) for n in numbers]
    elif force:
        nums = sorted(P._pdf_index().keys(), key=_num_key)
    else:
        nums = score_pending_numbers(db_path)
    if limit:
        nums = nums[:limit]

    if mapping is None:
        mapping = db.load_codepoint_map(db_path or db.DB_PATH)

    summary: ScoreSyncSummary = {"pending": len(nums), "processed": 0, "hymns": 0, "lines": 0,
                                 "lyrics": 0, "chars": 0, "align_ok": 0, "skipped": [],
                                 "seconds": 0.0}
    if not nums:
        return summary

    t0 = time.time()
    records = []
    for i, num in enumerate(nums, 1):
        try:
            rec = S.build_score(num, mapping=mapping)
        except Exception as exc:  # noqa: BLE001 - 单首失败不阻断整批
            summary["skipped"].append((num, f"{type(exc).__name__}: {exc}"))
            continue
        records.append(rec)
        summary["processed"] += 1
        summary["align_ok"] += 1 if _record_get(rec, "align_ok", 0) else 0
        if dry_run and not _record_get(rec, "lines"):
            # dry-run 不写库 → 这里就补上「无谱行」原因，避免报告里静默
            summary["skipped"].append((num, _record_get(rec, "review_reason") or "无曲谱行"))
        if on_rec is not None:
            on_rec(rec, i, len(nums))

    if records and not dry_run:
        stats = db.save_score_records(records, db_path or db.DB_PATH)
        summary["hymns"] = stats["hymns"]
        summary["lines"] = stats["lines"]
        summary["lyrics"] = stats["lyrics"]
        summary["chars"] = stats["chars"]
        summary["skipped"] += list(stats.get("skipped", []))
    summary["seconds"] = time.time() - t0
    return summary


def ppt_pending_files(ppt_dir: str | None = None, db_path: str | None = None) -> list[str]:
    """`data/赞美诗PPT/*.ppt` 中未入库或 `src_md5` 变化的文件（文件名序；目录缺失 → 空）"""
    ppt_dir = ppt_dir or J.PPT_DIR
    if not os.path.isdir(ppt_dir):
        return []
    conn = sqlite3.connect(db_path or _default_db_path())
    try:
        try:
            done = {str(f): (h or "") for f, h in
                    conn.execute("SELECT ppt_file, src_md5 FROM hymn_jianpu")}
        except sqlite3.OperationalError:  # v8 表尚未创建
            done = {}
    finally:
        conn.close()
    pending = []
    for name in sorted(f for f in os.listdir(ppt_dir) if f.lower().endswith(".ppt")):
        if done.get(name) != _md5_file(os.path.join(ppt_dir, name)):
            pending.append(name)
    return pending


def sync_ppt_jianpu(files=None, ppt_dir=None, dry_run=False, db_path=None,
                    on_rec=None) -> JianpuSyncSummary:
    """PPT 带简谱文字歌词（v8）→ `hymn_jianpu` / `hymn_jianpu_line` 入库（默认增量；幂等）

    Args:
        files: 指定 PPT 文件名列表（None → 只处理 `ppt_pending_files()`）
        ppt_dir: PPT 目录（默认 `ppt_jianpu.PPT_DIR`；目录不存在时返回全 0 汇总）
        dry_run: 只解析不写库
        db_path: 数据库路径（默认 `config.DB_PATH`）
        on_rec: 逐份回调 `(rec, i, total)`
    """
    from . import db

    ppt_dir = ppt_dir or J.PPT_DIR
    if not os.path.isdir(ppt_dir):
        return {"total": 0, "pending": 0, "processed": 0, "hymns": 0, "lines": 0,
                "skipped": [], "seconds": 0.0}
    total = len([f for f in os.listdir(ppt_dir) if f.lower().endswith(".ppt")])
    names = list(files) if files else ppt_pending_files(ppt_dir, db_path)
    summary: JianpuSyncSummary = {"total": total, "pending": len(names), "processed": 0,
                                  "hymns": 0, "lines": 0, "skipped": [], "seconds": 0.0}
    if not names:
        return summary

    t0 = time.time()
    records, _stats = J.extract_all(ppt_dir, db_path or db.DB_PATH,
                                    only=[str(n) for n in names])
    summary["processed"] = len(records)
    if on_rec is not None:
        for i, rec in enumerate(records, 1):
            on_rec(rec, i, len(records))
    if records and not dry_run:
        saved = db.save_jianpu_records(records, db_path or db.DB_PATH)
        summary["hymns"] = saved["hymns"]
        summary["lines"] = saved["lines"]
        summary["skipped"] = list(saved.get("skipped", []))
    summary["seconds"] = time.time() - t0
    return summary
