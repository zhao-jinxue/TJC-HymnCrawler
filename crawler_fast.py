# crawler_fast.py
# 🚀 终极极速版 - 统一入口
# 组装 crawler_core 各模块，提供菜单式交互

import os
import sys
import json
import time

from crawler_core.config import SAVE_ROOT, PROBE_REPORT, MAP_FILE
from crawler_core.db import (
    init_db, print_db_status, print_url_map_status,
    count_failed, get_failed_songs,
    sync_download_status_to_db
)
from crawler_core.driver import init_driver
from crawler_core.scanner import Scanner
from crawler_core.extractor import Extractor, load_url_map
from crawler_core.probe import run_probe, load_probe_report
from crawler_core.downloader import run_download


# ================= 打印横线 =================

def print_banner():
    print("=" * 60)
    print("🚀 真耶穌教會聖樂网爬虫 - 终极极速版")
    print("=" * 60)


def print_probe_report_status():
    """打印 probe_report.json 的状态和校验信息（过滤 _error 版本）"""
    if not os.path.exists(PROBE_REPORT):
        print("📋 资源探测报告（probe_report.json）：不存在")
        return

    with open(PROBE_REPORT, 'r', encoding='utf-8') as f:
        data = json.load(f)

    total = len(data)
    staff = sum(1 for d in data if d.get("staff_pdf"))
    num = sum(1 for d in data if d.get("numbered_pdf"))
    audio = sum(1 for d in data if d.get("audio_versions"))

    # 统计音频版本分布时，过滤掉 _error 键
    audio_key_count = {}
    audio_error_count = 0
    for d in data:
        av = d.get("audio_versions", {})
        for v in av:
            if v == "_error":
                audio_error_count += 1
            else:
                audio_key_count[v] = audio_key_count.get(v, 0) + 1

    no_staff = [d["hymn_number"] for d in data if not d.get("staff_pdf")]
    no_num = [d["hymn_number"] for d in data if not d.get("numbered_pdf")]
    no_audio = [d["hymn_number"] for d in data if not d.get("audio_versions")]

    # download_status 统计
    ds_counts = {}
    for d in data:
        s = d.get("download_status", "pending")
        ds_counts[s] = ds_counts.get(s, 0) + 1

    # integrity_status 统计
    is_counts = {}
    for d in data:
        s = d.get("integrity_status", "unchecked")
        is_counts[s] = is_counts.get(s, 0) + 1

    print(f"📋 资源探测报告（probe_report.json）：{total} 首")
    print(f"   五线谱: {staff}/{total} ({100*staff//total}%)" +
          (f" ⚠️ 缺: {no_staff}" if no_staff else " ✅"))
    print(f"   简谱:   {num}/{total} ({100*num//total}%)" +
          (f" ⚠️ 缺: {no_num}" if no_num else " ✅"))
    print(f"   有音频: {audio}/{total} ({100*audio//total}%)" +
          (f" ⚠️ 缺: {no_audio}" if no_audio else " ✅"))
    if audio_key_count:
        print(f"   音频版本分布:")
        for v, c in sorted(audio_key_count.items(), key=lambda x: -x[1]):
            print(f"     {v}: {c} ({100*c//total}%)")
    if audio_error_count > 0:
        print(f"   ⚠️ 探测异常（_error）: {audio_error_count} 首")
    if ds_counts:
        print(f"   下载状态分布:")
        for s, c in sorted(ds_counts.items(), key=lambda x: -x[1]):
            print(f"     {s}: {c}")
    if is_counts:
        print(f"   完整性状态分布:")
        for s, c in sorted(is_counts.items(), key=lambda x: -x[1]):
            print(f"     {s}: {c}")


# ================= 主菜单 =================

def main():
    print_banner()
    init_db()

    print("\n" + "=" * 40)
    print_db_status()
    print()
    print_url_map_status()
    print()
    print_probe_report_status()
    print("=" * 40)
    print()

    failed_count = count_failed()

    # 菜单项
    items = [
        ("1", "仅 Step 1：扫描列表页 + 创建目录"),
        ("2", "仅 Step 2：提取详情页文本（从 url_map.txt 读）"),
        ("3", "仅 资源探测（PDF HEAD + 音频页面点击）"),
        ("4", "仅 下载多媒体资源（根据 probe_report.json）"),
        ("5", "全流程：Step 1 → Step 2 → 资源探测 → 下载"),
    ]
    if failed_count > 0:
        items.append(("6", f"补全失败：重试提取 {failed_count} 首失败诗歌"))
    items.append(("0", "退出"))

    print("📋 请选择要执行的步骤：\n")
    for key, desc in items:
        print(f"  [{key}] {desc}")
    print()

    while True:
        try:
            choice = input("请输入选项 [0-6] (默认 5): ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "0"
            print()
        if choice == "":
            choice = "5"
            break
        elif choice in ("0", "1", "2", "3", "4", "5", "6"):
            break
        else:
            print("   无效选项，请输入 0-6")

    print()
    total_start = time.time()

    # ---- Step 1 ----
    if choice in ("1", "5"):
        scanner = Scanner()
        songs = scanner.scan()
        scanner.close()
        if not songs:
            print("❌ 未获取到数据，退出。")
            return

    # ---- Step 2 ----
    if choice in ("2", "5"):
        songs = load_url_map()
        if not songs:
            print("❌ url_map.txt 无数据，请先执行 Step 1。")
            return
        ext = Extractor()
        driver = init_driver()
        result = ext.extract_all(songs, driver)
        driver.quit()
        print(f"   ✅ Step 2：成功 {result['success']} 首，失败 {result['failed']} 首")

    # ---- 资源探测 ----
    if choice in ("3", "5"):
        if choice == "5":
            while True:
                try:
                    ans = input("👉 是否执行资源探测（PDF + 音频）？[Y/n] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "n"
                    print()
                if ans in ("", "y", "yes"):
                    probe_report = run_probe(force=True)
                    break
                elif ans in ("n", "no"):
                    print("⏭️ 跳过资源探测")
                    probe_report = load_probe_report()
                    break
                else:
                    print("   请输入 Y 或 N")
        else:
            probe_report = run_probe()

    # ---- 下载 ----
    if choice in ("4", "5"):
        if choice == "5":
            while True:
                try:
                    ans = input("👉 是否下载多媒体资源？[Y/n] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "n"
                    print()
                if ans in ("", "y", "yes"):
                    run_download(probe_report if 'probe_report' in dir() else None)
                    break
                elif ans in ("n", "no"):
                    print("⏭️ 跳过下载")
                    break
                else:
                    print("   请输入 Y 或 N")
        else:
            run_download()

    # ---- 补全 ----
    if choice == "6":
        failed_songs = get_failed_songs()
        if not failed_songs:
            print("✅ 没有需要补全的诗歌。")
        else:
            print(f"🔍 发现 {len(failed_songs)} 首提取失败的诗歌：")
            for s in failed_songs[:10]:
                print(f"     - {s['hymn_number']} {s['title'][:30]}")
            if len(failed_songs) > 10:
                print(f"     ... 共 {len(failed_songs)} 首")
            print()
            while True:
                try:
                    ans = input("👉 是否补全提取？[Y/n] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "n"
                    print()
                if ans in ("", "y", "yes"):
                    ext = Extractor()
                    driver = init_driver()
                    result = ext.extract_all(failed_songs, driver)
                    driver.quit()
                    print(f"   ✅ 补全完成：成功 {result['success']}，失败 {result['failed']}")
                    break
                elif ans in ("n", "no"):
                    break
                else:
                    print("   请输入 Y 或 N")

    # ---- 完成 ----
    elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"🎉 程序执行完成！总耗时: {elapsed:.1f}秒")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
