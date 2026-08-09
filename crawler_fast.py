# crawler_fast.py
# 🚀 终极极速版 - 统一入口
# 组装 crawler_core 各模块，提供菜单式交互

import json
import os
import time

from crawler_core.checksums import run as run_step6
from crawler_core.config import PROBE_REPORT
from crawler_core.db import (
    count_failed,
    get_failed_songs,
    init_db,
    print_db_status,
    print_url_map_status,
)
from crawler_core.db import (
    update_png_paths as run_step7,
)
from crawler_core.downloader import run_download
from crawler_core.driver import init_driver
from crawler_core.extractor import Extractor, load_url_map
from crawler_core.images import run as run_step5
from crawler_core.probe import load_probe_report, run_probe
from crawler_core.scanner import Scanner
from crawler_core.verify import main as run_step4

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
        print("   音频版本分布:")
        for v, c in sorted(audio_key_count.items(), key=lambda x: -x[1]):
            print(f"     {v}: {c} ({100*c//total}%)")
    if audio_error_count > 0:
        print(f"   ⚠️ 探测异常（_error）: {audio_error_count} 首")
    if ds_counts:
        print("   下载状态分布:")
        for s, c in sorted(ds_counts.items(), key=lambda x: -x[1]):
            print(f"     {s}: {c}")
    if is_counts:
        print("   完整性状态分布:")
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
        ("5", "校验与报告（第四阶段：数据对账 + 资源核验 + final_report）"),
        ("6", "仅 转图片：PDF→窄边距 PNG + 双页拼接 + 图片路径入库 + 哈希清单"),
        ("7", "全流程：Step 1 → Step 2 → 资源探测 → 下载 → 校验 → 转图片入库"),
    ]
    if failed_count > 0:
        items.append(("8", f"补全失败：重试提取 {failed_count} 首失败诗歌"))
    items.append(("0", "退出"))

    print("📋 请选择要执行的步骤：\n")
    for key, desc in items:
        print(f"  [{key}] {desc}")
    print()

    while True:
        try:
            choice = input("请输入选项 [0-8] (默认 5): ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "0"
            print()
        if choice == "":
            choice = "5"
            break
        elif choice in ("0", "1", "2", "3", "4", "5", "6", "7", "8"):
            break
        else:
            print("   无效选项，请输入 0-8")

    print()
    total_start = time.time()

    # ---- Step 1 ----
    if choice in ("1", "7"):
        scanner = Scanner()
        try:
            songs = scanner.scan()
        finally:
            scanner.close()
        if not songs:
            print("❌ 未获取到数据，退出。")
            return

    # ---- Step 2 ----
    if choice in ("2", "7"):
        songs = load_url_map()
        if not songs:
            print("❌ url_map.txt 无数据，请先执行 Step 1。")
            return
        ext = Extractor()
        driver = init_driver()
        try:
            result = ext.extract_all(songs, driver)
            print(f"   ✅ Step 2：成功 {result['success']} 首，失败 {result['failed']} 首")
        finally:
            driver.quit()

    # ---- 资源探测 ----
    if choice in ("3", "7"):
        if choice == "7":
            while True:
                try:
                    ans = input("👉 是否执行资源探测（PDF + 音频）？[y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "n"
                    print()
                if ans in ("y", "yes"):
                    probe_report = run_probe(force=True)
                    break
                elif ans in ("", "n", "no"):
                    print("⏭️ 跳过资源探测（使用现有 probe_report.json）")
                    probe_report = load_probe_report()
                    break
                else:
                    print("   请输入 Y 或 N")
        else:
            probe_report = run_probe()

    # ---- 下载 ----
    if choice in ("4", "7"):
        if choice == "7":
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

    # ---- 校验与报告 ----
    # 选项 5：独立执行校验；选项 7：全流程的中间一步（其后转图片入库）
    if choice in ("5", "7"):
        run_step4()

    # ---- 转图片入库（Step5 + Step7 + Step6）----
    # 选项 6：独立执行；选项 7：全流程的最后一步
    if choice in ("6", "7"):
        print("\n🖼️  转图片入库：PDF → 窄边距 PNG → 图片路径入库 → 哈希清单...")
        print("----- Step 5: PDF → 窄边距 PNG（含双页拼接） -----")
        run_step5()
        print("\n----- Step 7: 图片路径写入数据库新增字段 -----")
        run_step7()
        print("\n----- Step 6: 增量更新 checksums.json PNG 哈希（已有条目跳过） -----")
        run_step6(incremental=True)

    # ---- 补全 ----
    if choice == "8":
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
                    try:
                        result = ext.extract_all(failed_songs, driver)
                        print(f"   ✅ 补全完成：成功 {result['success']}，失败 {result['failed']}")
                    finally:
                        driver.quit()
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
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断，已保存的部分进度可断点续跑。")
    except Exception as e:  # noqa: BLE001 - 顶层兜底, 保证从容退出且已持久化数据不丢失
        print(f"\n❌ 程序异常退出：{type(e).__name__}: {e}")
        print("   已完成步骤已持久化，重新运行可断点续跑。")
