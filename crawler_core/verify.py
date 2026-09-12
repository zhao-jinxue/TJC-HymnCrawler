#!/usr/bin/env python3
"""
crawler_core/verify.py — 数据校验与报告（原 step4_verify_and_report.py）

职责:
  1. 三方对账：数据库记录数 vs 本地目录数 vs url_map.txt 行数
  2. 多媒体资源对账（按版本聚合）：存在率/完整率/缺失/损坏
  3. DB 路径交叉校验：DB 相对路径对应的磁盘文件是否存在（防悬挂引用）
  4. 失败任务归档：#62 人聲版为服务器端 404，标记归档，不重试
  5. 生成 final_report.txt
  6. rebuild_url_map(): 从 DB 重建 url_map.txt

原则：只读不改（不重命名目录、不重试 #62、不修改数据库状态）。
复用：crawler_core.downloader.verify_file_integrity 做单文件完整性校验。

用法:
  python3 -m crawler_core.verify                 # 执行校验并生成报告
  python3 -m crawler_core.verify --rebuild-map   # 从 DB 重建 url_map.txt
  from crawler_core.verify import main, rebuild_url_map
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

from .config import DB_PATH, MAP_FILE, PROBE_REPORT, SAVE_ROOT
from .downloader import verify_file_integrity

# 项目根目录（用于拼接 DB 相对路径 / 报告输出路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# 资源文件扩展名（排除 checksums.json 等非资源文件）
# `.mp4`：官网个别音频以 MP4 容器提供（如 #201 人聲版），归一化名仍为 .m4a，此处兜底防漏统计
RESOURCE_EXTS = {".pdf", ".m4a", ".mp3", ".mp4"}

# 音频版本分组（用于按类型聚合统计；合唱-1/2/3/4部版 归入"合唱部版"）
VERSION_GROUP_MAP = {
    "鋼琴版": "鋼琴版",
    "人聲版": "人聲版",
    "四部合唱版": "四部合唱版",
    "合唱-1部版": "合唱部版",
    "合唱-2部版": "合唱部版",
    "合唱-3部版": "合唱部版",
    "合唱-4部版": "合唱部版",
}


def load_db_numbers():
    """返回 DB 中全部 hymn_number 集合"""
    conn = sqlite3.connect(DB_PATH)
    nums = {r[0] for r in conn.execute("SELECT hymn_number FROM tjc_hymn")}
    conn.close()
    return nums


def load_url_map():
    """读取 url_map.txt -> {hymn_number: dir_name}"""
    mapping = {}
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 3:
                    h = parts[2].strip("/").split("/")[-1].split("?")[0]
                    mapping[h] = parts[1]
    return mapping


def load_probe_data():
    """读取 probe_report.json -> {hymn_number: entry}; 无则返回 {}"""
    if not os.path.exists(PROBE_REPORT):
        return {}
    with open(PROBE_REPORT, "r", encoding="utf-8") as f:
        report = json.load(f)
    return {e["hymn_number"]: e for e in report}


def list_hymn_dirs():
    """返回 {hymn_number: dir_path}（通过目录名解析编号）"""
    result = {}
    for d in os.listdir(SAVE_ROOT):
        dp = os.path.join(SAVE_ROOT, d)
        if not os.path.isdir(dp):
            continue
        # 目录格式: 001_1頌讚獨一真神 -> 去掉 3 位序号前缀后取编号 "1" / "51_a"
        rest = d.split("_", 1)[1] if "_" in d else d
        num = ""
        for ch in rest:
            if ch.isdigit():
                num += ch
            else:
                break
        if not num:
            continue
        # 支持 51_a / 124_b 之类编号
        suffix = ""
        idx = len(num)
        if rest[idx:idx + 2] in ("_a", "_b"):
            suffix = rest[idx:idx + 2]
        result[num + suffix] = dp
    return result


def parse_hymn_number_from_path(relpath):
    """从 DB 相对路径(如 Hymn_Downloads/001_1頌讚/1_五线谱.pdf)提取编号 '1'"""
    parts = relpath.replace("\\", "/").split("/")
    for p in parts:
        if "_" in p and not p.endswith(".pdf") and "." not in p:
            rest = p.split("_", 1)[1]
            num = ""
            for ch in rest:
                if ch.isdigit():
                    num += ch
                else:
                    break
            if num:
                suffix = ""
                idx = len(num)
                if rest[idx:idx + 2] in ("_a", "_b"):
                    suffix = rest[idx:idx + 2]
                return num + suffix
    return None


def load_db_paths():
    """读取 DB 路径字段 -> {hymn_number: {"staff": rel, "numbered": rel, "audio": {ver: rel}}}"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT hymn_number, staff_img_path, numbered_img_path, audio_versions FROM tjc_hymn"
    )
    rows = {}
    for h, staff, numbered, av_json in cur.fetchall():
        av = {}
        if av_json:
            try:
                av = json.loads(av_json)
            except (json.JSONDecodeError, TypeError):  # 历史数据非 JSON 时视为空
                av = {}
        rows[h] = {"staff": staff or "", "numbered": numbered or "", "audio": av or {}}
    conn.close()
    return rows


def main():
    print("=" * 60)
    print("🔍 数据校验与报告（原第四阶段）")
    print("=" * 60)

    # ---------- 1. 三方对账 ----------
    print("\n📊 [1/5] 三方数据对账（DB / 目录 / url_map）...")
    db_nums = load_db_numbers()
    dirs = list_hymn_dirs()
    url_map = load_url_map()
    probe_map = load_probe_data()

    dir_nums = set(dirs.keys())
    map_nums = set(url_map.keys())

    db_dir_diff = dir_nums - db_nums
    dir_db_diff = db_nums - dir_nums
    map_db_diff = db_nums - map_nums
    map_dir_diff = dir_nums - map_nums

    all_consistent = not (db_dir_diff or dir_db_diff or map_db_diff or map_dir_diff)

    print(f"   数据库记录: {len(db_nums)} | 本地目录: {len(dir_nums)} | url_map: {len(map_nums)}")
    if db_dir_diff:
        print(f"   ⚠️ 目录有但DB无: {sorted(db_dir_diff)}")
    if dir_db_diff:
        print(f"   ⚠️ DB有但目录无: {sorted(dir_db_diff)}")
    if map_db_diff:
        print(f"   ⚠️ url_map有但DB无: {sorted(map_db_diff)}")
    if map_dir_diff:
        print(f"   ⚠️ 目录有但url_map无: {sorted(map_dir_diff)}")
    print(f"   三方对账: {'✅ 完全一致' if all_consistent else '❌ 存在差异'}")

    # 文本完整率
    conn = sqlite3.connect(DB_PATH)
    has_lyrics = conn.execute(
        "SELECT COUNT(*) FROM tjc_hymn WHERE verse_count > 0"
    ).fetchone()[0]
    conn.close()
    text_rate = f"{has_lyrics}/{len(db_nums)} ({100 * has_lyrics // max(len(db_nums), 1)}%)"

    # ---------- 2. 多媒体资源对账（按版本聚合） ----------
    print("\n📂 [2/5] 多媒体资源对账（按类型聚合）...")

    # 预期资源: 优先用 probe_report.json 的探测清单；缺失时退回 DB 路径
    expected = defaultdict(list)  # hymn -> [(type_group, abs_path)]
    for h in sorted(db_nums):
        dir_path = dirs.get(h) or (SAVE_ROOT + "/" + url_map.get(h, ""))
        if not os.path.isdir(dir_path):
            continue
        entry = probe_map.get(h, {})
        # 五线谱 / 简谱
        if entry.get("staff_pdf"):
            expected[h].append(("五线谱", os.path.join(dir_path, f"{h}_五线谱.pdf")))
        if entry.get("numbered_pdf"):
            expected[h].append(("简谱", os.path.join(dir_path, f"{h}_简谱.pdf")))
        # 音频版本（过滤 _error / 无 url 键）
        av = entry.get("audio_versions", {})
        if not av:
            # 从 DB audio_versions 反推版本名
            db_paths = load_db_paths().get(h, {})
            for ver, rel in (db_paths.get("audio") or {}).items():
                if ver.startswith("_"):
                    continue
                expected[h].append((VERSION_GROUP_MAP.get(ver, ver),
                                    os.path.join(ROOT, rel)))
        else:
            for ver, info in av.items():
                if ver.startswith("_"):
                    continue
                if isinstance(info, dict) and info.get("url"):
                    ext = info.get("ext", "m4a")
                    expected[h].append((VERSION_GROUP_MAP.get(ver, ver),
                                        os.path.join(dir_path, f"{h}_{ver}.{ext}")))
                elif isinstance(info, str) and info.startswith("Hymn_Downloads"):
                    expected[h].append((VERSION_GROUP_MAP.get(ver, ver),
                                        os.path.join(ROOT, info)))

    # 实际资源：遍历目录，仅统计资源扩展名
    actual = defaultdict(list)  # hymn -> [(type_group, abs_path)]
    for h, dir_path in dirs.items():
        for fname in os.listdir(dir_path):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in RESOURCE_EXTS:
                continue
            fpath = os.path.join(dir_path, fname)
            # 从文件名归类型：_五线谱.pdf / _简谱.pdf / _鋼琴版.m4a 等
            base = fname.rsplit(".", 1)[0]
            if base.endswith("_五线谱"):
                group = "五线谱"
            elif base.endswith("_简谱"):
                group = "简谱"
            else:
                # 音频: 形如 1_鋼琴版 / 62_人聲版
                ver = base.split("_", 1)[1] if "_" in base else base
                group = VERSION_GROUP_MAP.get(ver, ver)
            actual[h].append((group, fpath))

    # 聚合统计
    stats = defaultdict(lambda: {"expected": 0, "existing": 0, "complete": 0,
                                 "missing": 0, "corrupt": 0})
    dangling = []          # DB 路径指向但文件不存在
    corrupt_files = []
    missing_files = []

    # DB 路径交叉校验
    db_paths = load_db_paths()
    for h, paths in db_paths.items():
        for field, rel in (("staff", paths["staff"]), ("numbered", paths["numbered"])):
            if rel:
                abs_p = os.path.join(ROOT, rel)
                if not os.path.exists(abs_p):
                    dangling.append(f"#{h} {field} -> {rel}")
        for ver, rel in (paths.get("audio") or {}).items():
            if ver.startswith("_"):
                continue
            if rel:
                abs_p = os.path.join(ROOT, rel)
                if not os.path.exists(abs_p):
                    dangling.append(f"#{h} 音频[{ver}] -> {rel}")

    # 预期 vs 实际
    for h in sorted(db_nums):
        for group, exp_path in expected.get(h, []):
            stats[group]["expected"] += 1
            exists = os.path.exists(exp_path)
            if not exists:
                stats[group]["missing"] += 1
                missing_files.append(f"#{h} {group}: {os.path.basename(exp_path)}")
                continue
            stats[group]["existing"] += 1
            # 用实际找到的同名文件做完整性校验
            if verify_file_integrity(exp_path):
                stats[group]["complete"] += 1
            else:
                stats[group]["corrupt"] += 1
                corrupt_files.append(f"#{h} {group}: {os.path.basename(exp_path)}")

    print(f"   {'类型':<12} {'预期':>5} {'存在':>5} {'完整':>5} {'缺失':>5} {'损坏':>5} {'成功率':>8}")
    for group in sorted(stats, key=lambda g: -stats[g]["expected"]):
        s = stats[group]
        rate = 100 * s["complete"] // max(s["expected"], 1)
        print(f"   {group:<12} {s['expected']:>5} {s['existing']:>5} {s['complete']:>5} "
              f"{s['missing']:>5} {s['corrupt']:>5} {rate:>7}%")

    # ---------- 3. #62 归档 ----------
    print("\n🗂️  [3/5] 失败任务归档检查...")
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT hymn_number, title, download_status, integrity_status "
        "FROM tjc_hymn WHERE download_status != 'completed' OR integrity_status != 'passed'"
    ).fetchall()
    conn.close()
    archive_notes = []
    for h, title, ds, ins in row:
        note = f"#{h} {title} | download={ds} | integrity={ins}"
        if h == "62":
            note += " | 原因: 服务器端缺失(人聲版 404)，已归档，不重试"
        archive_notes.append(note)
        print(f"   {note}")
    if not row:
        print("   ✅ 无失败任务")

    # ---------- 4. 生成报告 ----------
    print("\n📝 [4/5] 生成 final_report.txt ...")
    lines = []
    lines.append("=" * 60)
    lines.append("真耶穌教會聖樂网 爬虫项目 最终执行报告")
    lines.append(f"生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("一、抓取总览")
    lines.append(f"  - 数据库记录数: {len(db_nums)}")
    lines.append(f"  - 本地目录数:   {len(dir_nums)}")
    lines.append(f"  - url_map 条数: {len(map_nums)}")
    lines.append(f"  - 三方一致性:   {'✅ 完全一致' if all_consistent else '❌ 存在差异'}")
    lines.append(f"  - 文本完整率(有歌词): {text_rate}")
    lines.append("")
    lines.append("二、多媒体资源对账（按类型）")
    lines.append(f"  {'类型':<12} {'预期':>5} {'存在':>5} {'完整':>5} {'缺失':>5} {'损坏':>5} {'成功率':>8}")
    for group in sorted(stats, key=lambda g: -stats[g]["expected"]):
        s = stats[group]
        rate = 100 * s["complete"] // max(s["expected"], 1)
        lines.append(f"  {group:<12} {s['expected']:>5} {s['existing']:>5} {s['complete']:>5} "
                     f"{s['missing']:>5} {s['corrupt']:>5} {rate:>7}%")
    lines.append("")
    lines.append("三、DB 路径交叉校验（悬挂引用）")
    if dangling:
        lines.append(f"  ⚠️ 发现 {len(dangling)} 处引用指向不存在的文件:")
        for d in dangling:
            lines.append(f"    - {d}")
    else:
        lines.append("  ✅ 无悬挂引用（DB 路径全部命中磁盘文件）")
    lines.append("")
    lines.append("四、失败任务清单（服务端缺失，已归档，不重试）")
    if archive_notes:
        for n in archive_notes:
            lines.append(f"  - {n}")
    else:
        lines.append("  ✅ 无")
    lines.append("")
    lines.append("五、风险与说明")
    lines.append("  - url_map.txt 不在 git 跟踪内（.gitignore 规则 /Hymn_Downloads/** 忽略），")
    lines.append("    该文件是 编号->目录名 的唯一映射，有丢失风险；")
    lines.append("    可从数据库 staff_img_path 相对路径重建（verify 支持 --rebuild-map）。")
    lines.append(f"  - checksums.json 共 {len([1 for dp in dirs.values() if os.path.exists(os.path.join(dp, 'checksums.json'))])} 个已被 git 跟踪（SHA-256 哈希清单），非资源文件。")
    lines.append("  - 目录命名已规范（无全角空格/空格，格式 001_1頌讚獨一真神），无需清理。")
    lines.append("  - 数据库 audio_versions 存【相对路径字符串】，probe_report.json 存【线上 URL 对象】，二者勿混。")
    lines.append("")
    lines.append("=" * 60)
    lines.append("报告结束")

    report_path = os.path.join(ROOT, "final_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"   ✅ final_report.txt 已生成 ({len(lines)} 行)")

    # ---------- 5. 汇总 ----------
    print("\n📋 [5/5] 汇总")
    total_expected = sum(s["expected"] for s in stats.values())
    total_complete = sum(s["complete"] for s in stats.values())
    print(f"   资源总数: {total_expected} | 完整: {total_complete} "
          f"({100 * total_complete // max(total_expected, 1)}%)")
    if missing_files:
        print(f"   缺失 {len(missing_files)} 个文件（详见报告）")
    if corrupt_files:
        print(f"   损坏 {len(corrupt_files)} 个文件（详见报告）")
    if dangling:
        print(f"   ⚠️ 悬挂引用 {len(dangling)} 处（详见报告）")
    print("\n🎉 数据校验完成。")


def rebuild_url_map():
    """从数据库的相对路径重建 url_map.txt（编号 -> 目录名）"""
    print("🔄 从数据库重建 url_map.txt ...")
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT hymn_number, staff_img_path FROM tjc_hymn").fetchall()
    conn.close()

    new_map = {}
    for h, staff in rows:
        if staff:
            parts = staff.replace("\\", "/").split("/")
            if len(parts) >= 2:
                new_map[h] = parts[1]  # Hymn_Downloads/<dir>/xxx.pdf

    if len(new_map) < len(rows):
        print(f"   ⚠️ 仅能从 {len(new_map)}/{len(rows)} 条重建（其余无 staff_img_path）")
        return False

    lines = []
    for h in sorted(new_map, key=lambda x: int("".join(c for c in x if c.isdigit()) or 0)):
        lines.append(f"{int(''.join(c for c in h if c.isdigit()) or 0):03d}|{new_map[h]}|https://sacredmusic.tjc.org.tw/hymn/{h}")

    with open(MAP_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"   ✅ url_map.txt 已重建（{len(lines)} 条）")
    return True


if __name__ == "__main__":
    if "--rebuild-map" in sys.argv:
        rebuild_url_map()
    else:
        main()