#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 merged_all.txt 按三级结构（大类→小类→诗名→编号）转换为 JSON（数组版）。

结构（方案 A，支持同名歌）：
{
  "大类": {
    "小类": [
      {"诗名": 编号},
      {"诗名": 编号},
      ...
    ]
  }
}

- 小类下条目为数组，每个元素是 {"诗名": 编号}，因此同名歌（如
  "时刻交托主" 153 与 439）可同时存在，互不覆盖
- 跳过 "===== X.Y.txt =====" 分隔标记行
- 【大类】-> 一级节点；无编号行 -> 小类标题；"诗名 编号" -> 条目
- 跨页分页导致的顶部无标题条目，按"最近大类/最近小类"归属
- 已知结构特例：无独立小类的分类，用大类名作为默认小类（参照附录惯例）
  - 婚丧礼仪：5 首诗直接平铺（圣徒婚礼 298~安葬 302），无小类
  - 附录：无独立小类标题，条目归默认小类"附录"
- 输出：识别结果/merged_all.json
"""

import json
import re

SRC = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录/识别结果/merged_all.txt"
OUT = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录/识别结果/merged_all.json"

CAT_RE = re.compile(r"^【(.+?)】$")
ENTRY_RE = re.compile(r"^(.*?)\s+(\d+)$")

# 已知结构特例：无独立小类的分类，用大类名作为默认小类（参照附录的惯例）
# - 婚丧礼仪：5 首诗直接平铺（圣徒婚礼 298~安葬 302），无小类，编号原样保留
# - 附录：无独立小类标题，条目归默认小类"附录"
KNOWN_STRUCT = {
    "婚丧礼仪": {"subtitle": "婚丧礼仪", "first_line_is_subtitle": False},
    "附录": {"subtitle": "附录", "first_line_is_subtitle": False},
}


def main() -> None:
    with open(SRC, encoding="utf-8") as f:
        lines = f.read().splitlines()

    result: dict = {}
    cur_cat = None
    cur_sub = None
    pending_first = False  # 当前大类内首行（含编号）需按小类标题处理
    unclass_notes: list[str] = []

    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("====="):
            continue

        # 大类
        m = CAT_RE.match(s)
        if m:
            cur_cat = m.group(1).strip()
            result.setdefault(cur_cat, {})
            spec = KNOWN_STRUCT.get(cur_cat)
            if spec:
                cur_sub = spec["subtitle"]
                result[cur_cat].setdefault(cur_sub, [])
                pending_first = spec["first_line_is_subtitle"]
            else:
                cur_sub = None
                pending_first = False
            continue

        # 条目（以数字结尾）：诗名 编号
        m = ENTRY_RE.match(s)
        if m:
            name, num = m.group(1).strip(), int(m.group(2))
            if pending_first:
                continue  # 预留：如需特殊首行处理可在此扩展
            if cur_cat is None:
                cur_cat = "未分类"
                result.setdefault(cur_cat, {})
            if cur_sub is None:
                cur_sub = "_未分类"
                unclass_notes.append(f"{cur_cat} / {s}")
            result[cur_cat].setdefault(cur_sub, [])
            result[cur_cat][cur_sub].append({name: num})
            continue

        # 无编号行 -> 小类标题
        cur_sub = s
        pending_first = False
        if cur_cat is None:
            cur_cat = "未分类"
            result.setdefault(cur_cat, {})
        result[cur_cat].setdefault(cur_sub, [])

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    cats = len(result)
    subs = sum(len(v) for v in result.values())
    entries = sum(len(items) for subs_v in result.values() for items in subs_v.values())
    print(f"已保存: {OUT}")
    print(f"大类数: {cats}")
    print(f"小类数: {subs}")
    print(f"条目数: {entries}")
    if unclass_notes:
        print("\n[提示] 以下条目在所属大类下无小类标题，已归入 '_未分类'（建议人工复核）：")
        for n in unclass_notes:
            print(f"  - {n}")
    else:
        print("\n[提示] 无 '未分类' 条目。")


if __name__ == "__main__":
    main()