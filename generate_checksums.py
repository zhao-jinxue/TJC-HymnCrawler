#!/usr/bin/env python3
"""为 Hymn_Downloads 下每个目录生成 checksums.json（SHA-256 清单）。

大文件本体已被 .gitignore 忽略，无法直接经 git 感知其变更。
本脚本把每个目录内大文件的 SHA-256 哈希写入该目录的 checksums.json，
清单文件被 git 跟踪——大文件一旦被修改，重新提交时清单哈希即变化，
git status 即可感知"某个大文件被动过"。
"""
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent / "Hymn_Downloads"
EXTS = {".pdf", ".mp3", ".m4a", ".mp4", ".txt", ".png"}

# .gitignore 会忽略 Hymn_Downloads 下的非 .json 文件，
# 因此只有 checksums.json 会被 git 跟踪。
CHUNK = 1024 * 1024  # 1MB


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not BASE.is_dir():
        print(f"[SKIP] {BASE} 不存在，跳过", file=sys.stderr)
        return 0

    total = 0
    for entry in sorted(BASE.iterdir()):
        if not entry.is_dir():
            continue
        list_path = entry / "checksums.json"
        records = []
        for f in sorted(entry.iterdir()):
            if not f.is_file() or f.suffix.lower() not in EXTS:
                continue
            records.append({"file": f.name, "sha256": sha256_file(f)})
            total += 1
        # 目录下没有任何大文件时也生成空清单，保证目录结构可被跟踪
        with open(list_path, "w", encoding="utf-8") as fp:
            json.dump(records, fp, ensure_ascii=False, indent=2)
        print(f"[OK]   {entry.name} -> checksums.json ({len(records)} files)")
    print(f"[DONE] 共处理 {total} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())