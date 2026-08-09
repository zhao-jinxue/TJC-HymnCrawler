#!/usr/bin/env python3
"""
step6_update_img.py — 更新 checksums.json（图片哈希清单维护）

职责(本版本精简):
  遍历 Hymn_Downloads 下每个子目录, 将目录内所有 PNG 的 {file, sha256}
  追加/更新到该目录的 checksums.json。
  由 generate_checksums.py 兜底重建(pre-commit hook 也会调用该脚本),
  本脚本保障"PNG 增删后哈希清单同步"。

兼容性说明:
  - 不再改写 tjc_hymn.db 的 staff_img_path/numbered_img_path(保持 PDF 路径);
    图片路径由 step7_png_db.py 以新增字段 staff_png_path/numbered_png_path 维护。

用法:
  python3 step6_update_img.py
"""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)


def sha256_of(path):
    """计算文件 sha256"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_chks():
    """追加/更新 checksums.json 中的 PNG 条目; 返回更新的目录数"""
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


def run():
    """程序化入口: 供 crawler_fast.py 调用"""
    return run_chks()


def main():
    run()


if __name__ == "__main__":
    main()