#!/usr/bin/env python3
#   python crawler_api.py --step 11             # 只做音频时长统计（v10 audio_durations）
# crawler_api.py（原 crawler_fast.py，2026-09-13 重命名）
# 🚀 统一主入口（默认 API 引擎；Selenium 保底见 legacy/crawler_selenium.py）
#
# 用法（在项目根目录执行）：
#   python crawler_api.py                        # 交互菜单
#   python crawler_api.py --engine api --step 1  # 非交互：只跑 Step 1（API）
#   python crawler_api.py --engine selenium --step 7
#   python crawler_api.py --step check           # Step 1 三方一致性检查（不落盘）
#   python crawler_api.py --refresh-api-cache --step 2
#
# 目录约定（2026-09-13 目录重排）：
#   根目录只保留 README.md / crawler_api.py / tjc_hymn.db；
#   依赖与门禁配置 → config/，数据产物（probe_report.json、final_report.txt）→ data/，
#   Selenium 保底入口 → legacy/，Selenium 实现与 DOM 引擎 → crawler_core/selenium_legacy/。
#
# 引擎语义（§4.4）：api（默认，零浏览器依赖）/ selenium（旧 DOM 整链）/ auto（API 优先，逐首降级）

import argparse
import json
import os
import sys
import time

from crawler_core import api_client, naming, sync
from crawler_core.audio_duration import fill_durations
from crawler_core.checksums import run as run_step6
from crawler_core.config import PROBE_REPORT, VALID_ENGINES
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
from crawler_core.extractor import Extractor, load_url_map
from crawler_core.images import run as run_step5
from crawler_core.lyrics_api import run as run_lyrics_backfill
from crawler_core.probe import load_probe_report, run_probe, run_probe_missing
from crawler_core.scanner import Scanner, check_file
from crawler_core.verify import main as run_step4

# ================= 打印 =================

def print_banner(engine):
    print("=" * 60)
    print("🚀 真耶穌教會聖樂网爬虫 - 终极极速版")
    print(f"   引擎：{engine}（api=官网 JSON API / selenium=DOM 保底 / auto=API 优先逐首降级）")
    print("=" * 60)


def print_probe_report_status():
    """打印 probe_report.json 的状态与校验信息（过滤下划线元信息键）"""
    if not os.path.exists(PROBE_REPORT):
        print("📋 资源探测报告（probe_report.json）：不存在")
        return

    with open(PROBE_REPORT, 'r', encoding='utf-8') as f:
        data = json.load(f)

    total = len(data)
    staff = sum(1 for d in data if d.get("staff_pdf"))
    num = sum(1 for d in data if d.get("numbered_pdf"))
    audio = sum(1 for d in data if d.get("audio_versions"))

    audio_key_count = {}
    audio_error_count = 0
    for d in data:
        for v in d.get("audio_versions", {}):
            if v.startswith("_"):
                continue
            if v == "_error":
                audio_error_count += 1
            else:
                audio_key_count[v] = audio_key_count.get(v, 0) + 1

    no_staff = [d["hymn_number"] for d in data if not d.get("staff_pdf")]
    no_num = [d["hymn_number"] for d in data if not d.get("numbered_pdf")]
    no_audio = [d["hymn_number"] for d in data if not d.get("audio_versions")]

    ds_counts = {}
    for d in data:
        s = d.get("download_status", "pending")
        ds_counts[s] = ds_counts.get(s, 0) + 1

    is_counts = {}
    for d in data:
        s = d.get("integrity_status", "unchecked")
        is_counts[s] = is_counts.get(s, 0) + 1

    unavailable = api_client.count_unavailable(data)
    site_removed = sum(1 for d in data
                       for info in api_client.unavailable_items(d).values()
                       if info.get("_site_removed"))
    audio_available = sum(1 for d in data for v, info in (d.get("audio_versions") or {}).items()
                          if naming.is_audio_version_key(v) and api_client.is_available(info))

    print(f"📋 资源探测报告（probe_report.json）：{total} 首")
    print(f"   五线谱: {staff}/{total} ({100*staff//max(total,1)}%)" +
          (f" ⚠️ 缺: {no_staff}" if no_staff else " ✅"))
    print(f"   简谱:   {num}/{total} ({100*num//max(total,1)}%)" +
          (f" ⚠️ 缺: {no_num}" if no_num else " ✅"))
    print(f"   有音频: {audio}/{total} ({100*audio//max(total,1)}%) | 可用音频条目 {audio_available}" +
          (f" ⚠️ 缺: {no_audio}" if no_audio else " ✅"))
    if audio_key_count:
        print("   音频版本分布:")
        for v, c in sorted(audio_key_count.items(), key=lambda x: -x[1]):
            print(f"     {v}: {c} ({100*c//max(total,1)}%)")
    if audio_error_count > 0:
        print(f"   ⚠️ 探测异常（_error）: {audio_error_count} 首")
    if unavailable:
        print(f"   ℹ️ 源站不可用资源: {unavailable} 条（已摘出期望集合，不下载不重试）")
    if site_removed:
        print(f"   ℹ️ 官网已下架但本地留档（site_removed）: {site_removed} 条")
    if ds_counts:
        print("   下载状态分布:")
        for s, c in sorted(ds_counts.items(), key=lambda x: -x[1]):
            print(f"     {s}: {c}")
    if is_counts:
        print("   完整性状态分布:")
        for s, c in sorted(is_counts.items(), key=lambda x: -x[1]):
            print(f"     {s}: {c}")


# ================= 交互辅助 =================

def _ask_yes_no(prompt, default="y"):
    """交互式 yes/no（EOF/中断时取默认值），返回 bool"""
    while True:
        try:
            ans = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
            print()
        if ans == "":
            return default == "y"
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("   请输入 Y 或 N")


def _ask_probe_mode(engine):
    """资源探测方式选择：探测报告已有缺失音频时，让用户选全量重探或仅补缺失"""
    report = load_probe_report()
    if not report:
        return run_probe(engine=engine)
    missing = [e for e in report if not e.get("audio_versions")]
    if not missing:
        print("✅ probe_report.json 无缺失音频（全部条目都有 audio_versions）")
        return report
    if len(report) == len(missing):
        print("ℹ️ 所有条目都缺音频，直接全量重探。")
        return run_probe(force=True, engine=engine)
    print(f"  ⚠️ probe_report.json 有 {len(missing)} 首缺失音频: "
          f"{[e['hymn_number'] for e in missing]}")
    while True:
        try:
            ans = input("  补探方式？[1] 全量重探全部 [2] 仅补缺失音频 (默认 2): ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = "2"
            print()
        if ans == "1":
            return run_probe(force=True, engine=engine)
        if ans in ("", "2"):
            return run_probe_missing(engine=engine)


# ================= 步骤执行（可被 CLI 复用） =================

def run_step1(engine, use_cache=True):
    """Step 1：扫描列表 + 建目录（API / selenium / auto）"""
    scanner = Scanner(engine=engine)
    try:
        return scanner.scan(use_cache=use_cache)
    finally:
        scanner.close()


def run_step2(engine):
    """Step 2：详情提取（API 并发 / DOM 保底）"""
    songs = load_url_map()
    if not songs:
        print("❌ url_map.txt 无数据，请先执行 Step 1")
        return None
    extractor = Extractor(engine=engine)
    try:
        result = extractor.extract_all(songs, engine=engine)
        print(f"   ✅ 提取完成：成功 {result['success']}，失败 {result['failed']}，"
              f"跳过 {result['skipped']}")
        return result
    finally:
        extractor.close()


def run_step3(engine):
    """资源探测（API 清单 + URL 预检 / Selenium 保底）"""
    if engine == "api" and os.path.exists(PROBE_REPORT):
        return _ask_probe_mode(engine)
    return run_probe(engine=engine)


def print_audio_duration_summary(summary):
    """打印音频时长统计摘要（`audio_duration.fill_durations` 的汇总结构）"""
    print(f"   🔊 {summary['hymns']} 首 / {summary['entries']} 条音频"
          f"（读出 {summary['read']}，读不出 {summary['null']}）| "
          f"写入 {summary['written']} 行 / 未变 {summary['unchanged']} 行"
          f"（库内共 {summary['rows']} 首，本次处理 {summary['selected']} 首）")
    if summary["mismatch"]:
        print(f"   ⚠️ 键集不一致 {len(summary['mismatch'])} 行（audio_versions ≠ audio_version_list）："
              f"{summary['mismatch'][:3]}")
    if summary["issues"]:
        print(f"   ⚠️ 读不出时长的条目 {len(summary['issues'])} 个（值记 null）：")
        for no, ver, reason in summary["issues"][:5]:
            print(f"      #{no} {ver}: {reason}")


def run_step_audio(db_path=None):
    """音频时长统计入库（v10 `audio_durations`，与 `audio_version_list` 键集一一匹配）

    - **下载即入库**：`downloader._backfill_paths_to_db` 回写音频路径时已顺手写时长；
    - 本步骤是全流程末尾的**幂等兜底/补算**：覆盖「路径来自 probe 回填/历史数据/手工放入目录」
      等未走下载回写的场景，以及音频被替换后需要重算的情况（未变行只报告不写库）。
    """
    print("\n🔊 音频时长统计（写 tjc_hymn.audio_durations，键集与 audio_version_list 一一匹配）...")
    summary = fill_durations(db_path=db_path)
    print_audio_duration_summary(summary)
    return summary


def run_step10_full(engine, use_cache=True):
    """极速全量同步（纯 API）：Step1 → Step2 → 探测 → 下载 → 校验 → 音频时长"""
    print("\n⚡ 极速全量同步（API）：Step1 → Step2 → 资源探测 → 下载 → 校验 → 音频时长")
    run_step1(engine, use_cache=use_cache)
    run_step2(engine)
    report = run_probe(force=True, engine=engine)
    if report:
        run_download(report)
    run_step4()
    run_step_audio()


def run_step10_incremental(engine, use_cache=True):
    """增量同步（§5.7）：差异报表 → 确认落库 → 补探/补下载 → 校验"""
    print("\n⚡ 增量同步（水位 = api_raw.updated_at）")
    result = sync.run(apply=False, use_cache=use_cache)
    if result["plan"]["new"] or result["plan"]["changed"]:
        if _ask_yes_no("👉 是否按上述差异落库（更新 DB + 重建 hymn_category）？[y/N] ", default="n"):
            result = sync.run(apply=True, use_cache=use_cache)
        else:
            print("⏭️ 已跳过落库（仅报表）")
    if result["pending"] and _ask_yes_no("👉 是否存在新增音频待下载？[y/N] ", default="y"):
        run_probe_missing(engine=engine)
        run_download()
    run_step4()
    run_step_audio()  # 新增音频的时长补算（下载回写里已算过，这里兜底确保全覆盖）


# ================= 主菜单 =================

def build_menu(engine, failed_count):
    """菜单项（按引擎过滤：selenium 引擎下隐藏纯 API 的「10 极速同步」）"""
    items = [
        ("1", f"仅 Step 1：扫描列表页 + 创建目录（{engine}）"),
        ("2", f"仅 Step 2：提取详情页文本（{engine}，从 url_map.txt 读）"),
        ("3", f"仅 资源探测（{engine}：API 清单+URL 预检 / 音频点击）"),
        ("4", "仅 下载多媒体资源（根据 probe_report.json）"),
        ("5", "校验与报告（数据对账 + 资源核验 + final_report）"),
        ("6", "仅 转图片：PDF→窄边距 PNG + 双页拼接 + 图片路径入库 + 哈希清单"),
        ("7", "全流程：Step 1 → Step 2 → 资源探测 → 下载 → 校验 → 转图片入库"),
    ]
    if failed_count > 0:
        items.append(("8", f"补全失败：重试提取 {failed_count} 首失败诗歌"))
    items.append(("9", "歌词重抓：官网 API 全量刷新正歌 + 副歌 chorus（修复历史丢失）"))
    if engine != "selenium":
        items.append(("10", "极速全量同步（纯 API：全量 / 增量两模式）"))
    items.append(("11", "音频时长统计入库（v10 audio_durations，与版本列表一一匹配）"))
    items.append(("0", "退出"))
    return items



def main(argv=None, engine=None, step=None, use_cache=True):
    """统一入口：交互菜单 + CLI（--engine / --step）"""
    args = parse_args(argv)
    engine = (engine or args.engine or "api").lower()
    if engine not in VALID_ENGINES:
        print(f"⚠️ 未知引擎 {engine!r}，回落 api（可选：{'/'.join(VALID_ENGINES)}）")
        engine = "api"
    step = step or args.step
    use_cache = use_cache and not args.refresh_api_cache
    if args.refresh_api_cache:
        removed = api_client.cache_clear()
        print(f"♻️ 已清理 api_cache/{removed} 个分页缓存（--refresh-api-cache）")

    print_banner(engine)
    init_db()

    # ---- 非交互：--step 直接执行后退出 ----
    if step:
        return _run_single_step(step, engine, use_cache)

    # 无 TTY（cron/CI/管道）时不进入交互菜单，避免无限等待输入
    if not sys.stdin.isatty():
        print("⚠️ 非交互终端：请用 --step <1-11|check|incremental|audio> 指定要执行的步骤。")
        print("   示例：python crawler_api.py --engine api --step 10")
        return 2

    print("\n" + "=" * 40)
    print_db_status()
    print()
    print_url_map_status()
    print()
    print_probe_report_status()
    print("=" * 40)
    print()

    failed_count = count_failed()
    items = build_menu(engine, failed_count)
    valid = {k for k, _ in items}
    max_key = max(int(k) for k in valid)

    print("📋 请选择要执行的步骤：\n")
    for key, desc in items:
        print(f"  [{key}] {desc}")
    print()

    while True:
        try:
            choice = input(f"请输入选项 [0-{max_key}] (默认 5): ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "0"
            print()
        if choice == "":
            choice = "5"
            break
        if choice in valid:
            break
        print(f"   无效选项，请输入 {sorted(valid)}")

    print()
    total_start = time.time()
    _dispatch(choice, engine, failed_count, use_cache)

    elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"🎉 程序执行完成！总耗时: {elapsed:.1f}秒")
    print(f"{'='*60}")
    return 0


def _dispatch(choice, engine, failed_count, use_cache):
    """按菜单选项执行对应步骤（与重构前步骤语义一致；failed_count 仅用于日志语境）"""
    # ---- Step 1 ----
    if choice in ("1", "7"):
        songs = run_step1(engine, use_cache=use_cache)
        if not songs:
            print("❌ 未获取到数据，退出。")
            return

    # ---- Step 2 ----
    if choice in ("2", "7"):
        run_step2(engine)

    # ---- 资源探测 ----
    probe_report = None
    if choice in ("3", "7"):
        if choice == "3":
            probe_report = run_step3(engine)
        else:
            probe_report = run_probe(engine=engine)

    # ---- 下载 ----
    if choice in ("4", "7"):
        if choice == "7":
            if _ask_yes_no("👉 是否下载多媒体资源？[Y/n] "):
                report = probe_report if isinstance(probe_report, list) and probe_report else None
                run_download(report)
            else:
                print("⏭️ 跳过下载")
        else:
            run_download()

    # ---- 校验与报告 ----
    if choice in ("5", "7"):
        run_step4()

    # ---- 转图片入库（Step5 + Step7 + Step6）----
    if choice in ("6", "7"):
        print("\n🖼️  转图片入库：PDF → 窄边距 PNG → 图片路径入库 → 哈希清单...")
        print("----- Step 5: PDF → 窄边距 PNG（含双页拼接） -----")
        run_step5()
        print("\n----- Step 7: 图片路径写入数据库新增字段 -----")
        run_step7()
        print("\n----- Step 6: 增量更新 checksums.json PNG 哈希（已有条目跳过） -----")
        run_step6(incremental=True)

    # ---- 音频时长统计入库（v10；下载回写已算，这里兜底/补算）----
    if choice in ("7", "11"):
        run_step_audio()

    # ---- 补全失败 ----
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
            if _ask_yes_no("👉 是否补全提取？[Y/n] "):
                extractor = Extractor(engine=engine)
                try:
                    result = extractor.extract_all(failed_songs, resume=False, engine=engine)
                    print(f"   ✅ 补全完成：成功 {result['success']}，失败 {result['failed']}")
                finally:
                    extractor.close()

    # ---- 歌词重抓（官网 API：正歌 + 副歌 chorus）----
    if choice == "9":
        print("\n🎵 歌词重抓：逐首调用官网 API 刷新 verse_1..10 + chorus ...")
        print("   说明：官网把副歌单独放在 lyrics_chorus，旧版仅抓正歌首个片段导致副歌丢失。")
        run_lyrics_backfill()

    # ---- 极速全量同步（纯 API）----
    if choice == "10":
        print("\n⚡ 极速全量同步：")
        print("   [1] 全量（Step1+Step2+探测+下载+校验）  [2] 增量（差异报表+落库）")
        while True:
            try:
                ans = input("   请选择 (默认 1): ").strip()
            except (EOFError, KeyboardInterrupt):
                ans = "1"
                print()
            if ans in ("", "1"):
                run_step10_full("api", use_cache=use_cache)
                break
            if ans == "2":
                run_step10_incremental("api", use_cache=use_cache)
                break
            print("   请输入 1 或 2")




def _run_single_step(step, engine, use_cache):
    """CLI `--step`：执行单步（非交互）后返回退出码"""
    step = str(step).strip().lower()
    print(f"▶️ 非交互执行：step={step} | engine={engine}")
    total_start = time.time()

    if step == "check":
        return check_file()
    if step == "1":
        run_step1(engine, use_cache=use_cache)
    elif step == "2":
        run_step2(engine)
    elif step == "3":
        run_probe(force=True, engine=engine)
    elif step == "4":
        run_download()
    elif step == "5":
        run_step4()
    elif step == "6":
        run_step5()
        run_step7()
        run_step6(incremental=True)
    elif step == "7":
        run_step1(engine, use_cache=use_cache)
        run_step2(engine)
        report = run_probe(force=True, engine=engine)
        if report:
            run_download(report)
        run_step4()
        run_step5()
        run_step7()
        run_step6(incremental=True)
    elif step == "8":
        failed_songs = get_failed_songs()
        if not failed_songs:
            print("✅ 没有需要补全的诗歌。")
        else:
            print(f"🔍 补全提取 {len(failed_songs)} 首失败诗歌（引擎 {engine}）...")
            extractor = Extractor(engine=engine)
            try:
                extractor.extract_all(failed_songs, resume=False, engine=engine)
            finally:
                extractor.close()
    elif step == "9":
        run_lyrics_backfill()
    elif step == "10":
        run_step10_full(engine, use_cache=use_cache)
    elif step in ("11", "audio"):
        run_step_audio()
    elif step == "incremental":
        run_step10_incremental(engine, use_cache=use_cache)
    else:
        print(f"❌ 未知步骤 {step!r}（可选：1-11 / check / incremental / audio）")
        return 2

    print(f"\n🎉 step={step} 执行完成！耗时 {time.time()-total_start:.1f}s")
    return 0


def parse_args(argv=None):
    """CLI 参数：--engine / --step / --refresh-api-cache"""
    parser = argparse.ArgumentParser(
        description="TJC 聖樂网爬虫统一入口（默认 API 引擎，Selenium 保底）")
    parser.add_argument("--engine", choices=list(VALID_ENGINES), default="api",
                        help="抓取引擎（默认 api；auto=API 优先、逐首降级 DOM）")
    parser.add_argument("--step", default=None,
                        help="非交互执行单步：1-11 / check（Step1 一致性检查）/ incremental / audio（音频时长）")
    parser.add_argument("--refresh-api-cache", action="store_true",
                        help="忽略并重建 Hymn_Downloads/api_cache/ 分页缓存")
    args, _unknown = parser.parse_known_args(argv)
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断，已保存的部分进度可断点续跑。")
    except Exception as e:  # noqa: BLE001 - 顶层兜底, 保证从容退出且已持久化数据不丢失
        print(f"\n❌ 程序异常退出：{type(e).__name__}: {e}")
        print("   已完成步骤已持久化，重新运行可断点续跑。")
