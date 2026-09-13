#!/usr/bin/env python3
"""
crawler_core/checksums.py — checksums.json 哈希清单维护

职责（相同概念合并）:
  1. rebuild_all(): 全量重建每个诗歌目录的 checksums.json（扫全部 EXTS 文件；
     无可登记文件的目录跳过，不生成 `[]` 噪音文件）
     —— 原 generate_checksums.py
  2. update_png():  增量更新目录内 PNG 的 {file, sha256} 条目
     —— 原 step6_update_img.py

用法:
  python3 -m crawler_core.checksums               # 全量重建（默认, 与旧 generate_checksums 行为一致）
  python3 -m crawler_core.checksums --incremental  # 仅增量更新 PNG 条目
  from crawler_core.checksums import run, rebuild_all, update_png  # 程序化入口
"""
import argparse
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

BASE = os.path.join(ROOT, "Hymn_Downloads")
# Hymn_Downloads/** 下被 git 忽略的大文件扩展名（含 PNG 图片）
EXTS = {".pdf", ".mp3", ".m4a", ".mp4", ".txt", ".png"}
CHUNK = 1024 * 1024  # 1MB


def sha256_file(path):
    """计算文件 sha256"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_of(path):
    """计算文件 sha256（兼容旧 step6 命名）"""
    return sha256_file(path)


def rebuild_all(base=BASE):
    """全量重建每个目录的 checksums.json（原 generate_checksums.py 主逻辑）

    base: 资源根目录（默认 `Hymn_Downloads`；测试可指向临时目录）
    说明: 无可登记文件的目录**不新建** checksums.json（如 `api_cache/` 只有 JSON 缓存、
          `_misc/` 只有被忽略的安装包）——避免全量重建时冒出 `[]` 噪音文件。
    """
    if not os.path.isdir(base):
        print(f"[SKIP] {base} 不存在，跳过")
        return 0

    total = 0
    for entry in sorted(os.listdir(base)):
        if not os.path.isdir(os.path.join(base, entry)):
            continue
        list_path = os.path.join(base, entry, "checksums.json")
        records = []
        for f in sorted(os.listdir(os.path.join(base, entry))):
            fpath = os.path.join(base, entry, f)
            if not os.path.isfile(fpath) or os.path.splitext(f)[1].lower() not in EXTS:
                continue
            records.append({"file": f, "sha256": sha256_file(fpath)})
            total += 1
        if not records and not os.path.exists(list_path):
            print(f"[SKIP] {entry}（无可登记文件，不新建 checksums.json）")
            continue
        with open(list_path, "w", encoding="utf-8") as fp:
            json.dump(records, fp, ensure_ascii=False, indent=2)
        print(f"[OK]   {entry} -> checksums.json ({len(records)} files)")
    print(f"[DONE] 共处理 {total} 个文件")
    return total


def update_png():
    """增量更新各目录 checksums.json 中的 PNG 条目（原 step6_update_img.py 主逻辑）"""
    changed_dirs = 0
    for dirpath, _dirs, files in os.walk("Hymn_Downloads"):
        checksum_path = os.path.join(dirpath, "checksums.json")
        if not os.path.exists(checksum_path):
            continue
        with open(checksum_path, "r", encoding="utf-8") as fh:
            entries = json.load(fh)

        index = {e["file"]: i for i, e in enumerate(entries)}
        modified = False
        for fn in sorted(files):
            if fn.endswith(".png"):
                h = sha256_of(os.path.join(dirpath, fn))
                if fn in index:
                    if entries[index[fn]]["sha256"] != h:
                        entries[index[fn]]["sha256"] = h
                        modified = True
                else:
                    entries.append({"file": fn, "sha256": h})
                    modified = True

        if modified:
            with open(checksum_path, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            changed_dirs += 1

    print(f"[任务: checksums] 更新: {changed_dirs} 个目录")
    return changed_dirs


def run(incremental=False):
    """程序化入口: 供 crawler_api.py 调用"""
    if incremental:
        return update_png()
    return rebuild_all()


def main():
    parser = argparse.ArgumentParser(description="checksums.json 哈希清单维护")
    parser.add_argument("--incremental", action="store_true",
                        help="仅增量更新 PNG 条目(默认全量重建)")
    args = parser.parse_args()
    run(incremental=args.incremental)


if __name__ == "__main__":
    main()