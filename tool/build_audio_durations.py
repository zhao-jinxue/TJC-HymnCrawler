#!/usr/bin/env python3
"""tool/build_audio_durations.py — 统计每首各版本音频的时长，写入 `tjc_hymn.audio_durations`（v10）

为什么需要（2026-09-19）：
  库内原本只有音频**路径**（`audio_versions`）与**版本名列表**（`audio_version_list`），
  没有任何可展示/可排序的时长信息 → 展示层无法显示时长、无法按长度筛选或排序。
  本工具离线读取本地音频文件（`Hymn_Downloads/`），把时长写进新增列 `audio_durations`，
  结构为 `{版本名: 秒}`，与上面两列**键集一一匹配**（读不出的版本值落 `null`，键仍保留）。

时长来源（为什么按后缀显式选 mutagen 子类，2026-09-19 实测）：
  `mutagen.File()` 的**类型嗅探**对无 ID3 标签的 MP3 返回 None（本库 3 个：
  #138 / #197 / #296_b 人聲版），而 `mutagen.mp3.MP3(path)` / `mutagen.mp4.MP4(path)` 直接构造
  可正常读出（89.05 / 185.04 / 252.43 秒）。全库 1119 个音频实测 **1119 读出、0 失败**
  （27.2 – 389.2 秒，中位 154.5 秒）。实现见 `crawler_core/audio_duration.py`。

用法（项目根执行）：
  python tool/build_audio_durations.py                    # 全量统计并写库（幂等，可反复跑）
  python tool/build_audio_durations.py --only 1 5 349     # 只处理指定编号
  python tool/build_audio_durations.py --limit 20 --dry-run   # 抽样试跑，只统计不写库
  python tool/build_audio_durations.py --stats            # 只打印库内覆盖统计
  python tool/build_audio_durations.py --show 1           # 打印某首各版本时长（mm:ss）

注意：重新下载 / 替换音频后需重跑本工具（时长随音频文件变化）。
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from crawler_core import audio_duration as ad, db  # noqa: E402
from crawler_core.naming import is_audio_version_key  # noqa: E402


def fmt_seconds(seconds):
    """秒 → `m:ss`（None / 非数 → `—`）"""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return "—"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def load_rows(db_path):
    """读 `tjc_hymn` 的音频相关四列（复用 `audio_duration.load_version_rows`）"""
    return ad.load_version_rows(db_path)


def durations_of(audio_versions_json, root=None):
    """`audio_versions` JSON → `({版本名: 秒或 None}, {版本名: 失败原因})`

    复用 `audio_duration.durations_of`：只取**真实版本键**（`_` 前缀元信息键剔除），
    键集与 `audio_versions` 一致 → 写库后与 `audio_version_list` 一一匹配。
    """
    return ad.durations_of(audio_versions_json, root)


def run(numbers=None, limit=None, dry_run=False, db_path=None, root=None, quiet=False):
    """批量统计并写库 → 汇总 dict（实际工作复用 `audio_duration.fill_durations`）

    Args:
        numbers: 只处理这些编号（None / 空 = 全部）
        limit: 最多处理多少首
        dry_run: 只统计不写库
        db_path: 数据库路径（默认 `config.DB_PATH`）
        root: 相对路径的解析根（默认项目根；测试可注入临时目录）
        quiet: 不打印逐首明细
    """
    db_path = db_path or db.DB_PATH
    rows = ad.load_version_rows(db_path)
    total = len(rows)
    if numbers:
        wanted = {str(n) for n in numbers}
        selected = [r for r in rows if r[0] in wanted]
    else:
        selected = rows
    if limit:
        selected = selected[:limit]

    def _on_row(i, count, no, durations):
        detail = " | ".join(f"{ver} {fmt_seconds(sec)}" for ver, sec in durations.items())
        print(f"  [{i:>4}/{count}] #{no}  {len(durations)} 版本 | {detail}")

    print(f"🎵 待统计 {len(selected)} 首（库内共 {total} 首）…")
    t0 = time.time()
    summary = ad.fill_durations(db_path=db_path, numbers=numbers, limit=limit,
                                dry_run=dry_run, root=root,
                                on_row=None if quiet else _on_row)

    print(f"✅ 统计完成：{summary['hymns']} 首 / {summary['entries']} 个音频"
          f"（读出 {summary['read']} | 读不出 {summary['null']}）/ 耗时 {time.time() - t0:.1f}s")
    if dry_run:
        print(f"（--dry-run：未写库，本应更新 {summary['written']} 行）")
    else:
        print(f"💾 已写入 tjc_hymn.audio_durations：更新 {summary['written']} 行 / "
              f"未变 {summary['unchanged']} 行")
    if summary["mismatch"]:
        print(f"⚠️ 键集不一致 {len(summary['mismatch'])} 行（audio_versions ≠ audio_version_list）：")
        for no, keys, listed in summary["mismatch"][:10]:
            print(f"   #{no}: audio_versions={keys} / audio_version_list={listed}")
    else:
        print(f"🔗 键集校验：{summary['hymns']} 行的 audio_versions ↔ audio_version_list "
              f"↔ audio_durations 全部一致 ✅")
    if summary["issues"]:
        print(f"⚠️ 读不出时长的条目 {len(summary['issues'])} 个：")
        for no, ver, reason in summary["issues"][:10]:
            print(f"   #{no} {ver}: {reason}")
    return summary


def stats(db_path=None):
    """打印库内 `audio_durations` 覆盖统计（总时长 / 各版本平均时长 / 键集一致性）"""
    st = db.audio_duration_stats(db_path)
    print(f"📊 audio_durations 覆盖（{db_path or db.DB_PATH}）：")
    print(f"   已填时长: {st['filled']}/{st['rows']} 行")
    print(f"   音频条目: {st['entries_filled']}/{st['entries']} 条有秒数"
          f"（读不出 {st['entries_null']}）")
    h, rem = divmod(int(st["seconds_total"]), 3600)
    print(f"   总时长:   {h}h{rem // 60:02d}m（{st['seconds_total']:.0f} 秒）")
    print(f"   键集不一致: {st['mismatch']} 行"
          f"{' ✅' if not st['mismatch'] else '（跑一次不带参数的统计即可重建）'}")
    if st["by_version"]:
        print("   各版本平均时长:")
        for ver, (cnt, secs) in sorted(st["by_version"].items(), key=lambda x: -x[1][0]):
            print(f"     {ver}: {cnt} 条 / 平均 {fmt_seconds(secs / cnt if cnt else 0)}")
    return st


def show(num, db_path=None):
    """打印一首的音频时长明细（人读复核）"""
    rows = [r for r in load_rows(db_path or db.DB_PATH) if r[0] == str(num)]
    if not rows:
        print(f"#{num}：库内没有该编号")
        return 1
    no, av_json, vl_json, dur_json = rows[0]
    try:
        av = json.loads(av_json or "{}")
    except (json.JSONDecodeError, TypeError):
        av = {}
    try:
        durations = json.loads(dur_json or "{}")
    except (json.JSONDecodeError, TypeError):
        durations = {}
    keys = [k for k in av if is_audio_version_key(k)] if isinstance(av, dict) else []
    print(f"\n{'=' * 78}\n#{no}  音频 {len(keys)} 版本（audio_version_list={vl_json}）")
    for ver, rel in av.items():
        if not is_audio_version_key(ver):
            continue
        sec = durations.get(ver)
        path = ad.resolve_audio_path(rel if isinstance(rel, str) else "")
        exists = "✔" if path and os.path.isfile(path) else "✗"
        print(f"  {ver:<8} {fmt_seconds(sec):>7}  ({sec if sec is not None else '未统计'})  {exists} {rel}")
    if set(durations) != set(keys):
        print("  ⚠️ 键集与 audio_versions 不一致（重跑 python tool/build_audio_durations.py）")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="统计音频时长并写入 tjc_hymn.audio_durations（v10）")
    ap.add_argument("numbers", nargs="*", help="诗歌编号（缺省 = 全部）")
    ap.add_argument("--only", nargs="*", default=None, help="同位置参数，便于脚本里显式书写")
    ap.add_argument("--limit", type=int, default=None, help="最多处理多少首")
    ap.add_argument("--db", default=db.DB_PATH, help=f"数据库（默认 {db.DB_PATH}）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    ap.add_argument("--stats", action="store_true", help="只打印库内统计")
    ap.add_argument("--show", nargs="*", default=None, help="打印指定编号的时长后退出")
    ap.add_argument("--quiet", action="store_true", help="不打印逐首明细")
    args = ap.parse_args(argv)

    if args.stats:
        stats(args.db)
        return 0
    if args.show:
        for n in args.show:
            show(n, args.db)
        return 0

    run(numbers=args.only or args.numbers, limit=args.limit, dry_run=args.dry_run,
        db_path=args.db, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
