# crawler_core/downloader.py
# Step 3: 多媒体资源下载管理器
# 根据 probe_report.json 的清单并发下载所有资源
# v2: +文件完整性校验 +同步状态到数据库

import json
import os
import time

import requests
import urllib3

urllib3.disable_warnings()
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import HEADERS, PROBE_REPORT, SAVE_ROOT
from .db import batch_update_integrity, sync_download_status_to_db


def run_download(probe_report=None):
    """
    执行资源下载
    probe_report: 可选，直接传入已加载的探测报告
    """
    if probe_report is None:
        if not os.path.exists(PROBE_REPORT):
            print("❌ probe_report.json 不存在，请先运行资源探测")
            return
        with open(PROBE_REPORT, 'r', encoding='utf-8') as f:
            probe_report = json.load(f)

    # ---- 初始化状态 ----
    for m in probe_report:
        if "download_status" not in m:
            m["download_status"] = "pending"
        if "integrity_status" not in m:
            m["integrity_status"] = "unchecked"

    total = len(probe_report)
    items = []

    for m in probe_report:
        h = m["hymn_number"]
        dir_path = _find_hymn_dir(h)
        if not dir_path:
            continue

        if m.get("staff_pdf"):
            items.append({
                "type": "五线谱", "hymn": h, "hymn_obj": m,
                "url": m["staff_pdf"],
                "path": os.path.join(dir_path, f"{h}_五线谱.pdf")
            })
        if m.get("numbered_pdf"):
            items.append({
                "type": "简谱", "hymn": h, "hymn_obj": m,
                "url": m["numbered_pdf"],
                "path": os.path.join(dir_path, f"{h}_简谱.pdf")
            })
        for ver, info in m.get("audio_versions", {}).items():
            if "url" in info and info["url"] and not ver.startswith("_"):
                ext = info.get("ext", "m4a")
                items.append({
                    "type": f"音频-{ver}", "hymn": h, "hymn_obj": m,
                    "url": info["url"],
                    "path": os.path.join(dir_path, f"{h}_{ver}.{ext}")
                })

    total_files = len(items)
    if total_files == 0:
        print("❌ 没有需要下载的资源")
        return

    print(f"\n📥 开始下载 {total_files} 个资源文件...")
    print(f"   歌曲数: {total} | 并发: 10 线程")

    # 跳过已存在的
    skipped = sum(1 for item in items if os.path.exists(item["path"]))
    if skipped > 0:
        print(f"   ⏭️ {skipped} 个文件已存在，跳过下载")
        items = [item for item in items if not os.path.exists(item["path"])]

    if not items:
        print("✅ 所有文件已下载完成！")
        # 更新状态 + 校验 + 同步数据库
        _update_download_status(probe_report)
        _verify_and_sync(probe_report)
        return

    start = time.time()
    success = 0
    fail = 0

    def download_one(item):
        try:
            resp = requests.get(item["url"], headers=HEADERS, timeout=30, verify=False)
            if resp.status_code == 200:
                os.makedirs(os.path.dirname(item["path"]), exist_ok=True)
                with open(item["path"], 'wb') as f:
                    f.write(resp.content)
                return (item, True)
            else:
                return (item, False)
        except Exception:  # noqa: BLE001 - 网络/写入异常视为下载失败, 返回 False
            return (item, False)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(download_one, item): item for item in items}
        done = 0
        for f in as_completed(futures):
            item, ok = f.result()
            done += 1  # noqa: SIM113 - 显式计数器便于进度显示(每20个刷新)
            if ok:
                success += 1
            else:
                fail += 1
                print(f"   ❌ [{item['type']}] #{item['hymn']}: {item['url']}")

            if done % 20 == 0 or done == len(items):
                elapsed = time.time() - start
                print(f"    ⏳ {done}/{len(items)} | 成功: {success} | 失败: {fail} | "
                      f"耗时 {elapsed:.0f}s | {done/elapsed:.1f}文件/s")

    elapsed = time.time() - start
    print("\n📊 下载完成！")
    print(f"   成功: {success}/{total_files} ({100*success//total_files}%)")
    print(f"   失败: {fail}")
    print(f"   耗时: {elapsed:.1f}s | 速度: {total_files/elapsed:.1f}文件/s")

    # 更新 download_status + 文件完整性校验 + 同步到数据库
    _update_download_status(probe_report)
    _verify_and_sync(probe_report)


# ================= 文件完整性校验 =================

def verify_file_integrity(filepath):
    """校验单个文件是否完整可读取

    Args:
        filepath: 文件路径
    Returns:
        bool: True 表示文件完整可读取
    """
    if not os.path.exists(filepath):
        return False

    try:
        size = os.path.getsize(filepath)
        if size == 0:
            return False

        ext = os.path.splitext(filepath)[1].lower()

        with open(filepath, 'rb') as f:
            header = f.read(16)

        if ext == '.pdf':
            # PDF 必须以 %PDF 开头
            return header[:4] == b'%PDF'

        elif ext == '.m4a':
            # M4A/MP4: ftyp box 开头
            if header[4:8] == b'ftyp':
                return True
            # 也可能是仅包含 moov 的格式
            return size > 100

        elif ext == '.mp3':
            # MP3: ID3 标签 或以 FF 帧同步开头
            if header[:3] == b'ID3':
                return True
            if header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
                return True
            return size > 100  # 无头但有点大小也算

        else:
            return size > 0

    except OSError:
        return False


def _verify_and_sync(probe_report):
    """对 probe_report 中所有 completed / partial 的诗歌做文件完整性校验，
       然后将 download_status + integrity_status 同步到数据库"""
    from .config import MAP_FILE

    # 构建 dir_map
    dir_map = {}
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    url = parts[2]
                    h = url.strip('/').split('/')[-1].split('?')[0]
                    dir_map[h] = parts[1]

    integrity_results = {}  # {hymn_number: "passed"|"failed"}

    for m in probe_report:
        h = m["hymn_number"]
        dir_name = dir_map.get(h)
        if not dir_name:
            m["integrity_status"] = "unchecked"
            continue

        dir_path = os.path.join(SAVE_ROOT, dir_name)
        if not os.path.isdir(dir_path):
            m["integrity_status"] = "unchecked"
            continue

        # 收集所有预期文件
        expected_files = []
        if m.get("staff_pdf"):
            expected_files.append(os.path.join(dir_path, f"{h}_五线谱.pdf"))
        if m.get("numbered_pdf"):
            expected_files.append(os.path.join(dir_path, f"{h}_简谱.pdf"))
        for ver, info in m.get("audio_versions", {}).items():
            if "url" in info and info["url"] and not ver.startswith("_"):
                ext = info.get("ext", "m4a")
                expected_files.append(os.path.join(dir_path, f"{h}_{ver}.{ext}"))

        if not expected_files:
            m["integrity_status"] = "unchecked"
            continue

        # 针对 download_status 为 completed 或 partial 的做校验
        ds = m.get("download_status", "pending")
        if ds in ("completed",) or ds.startswith("partial"):
            all_passed = True
            for fp in expected_files:
                if os.path.exists(fp):
                    if not verify_file_integrity(fp):
                        all_passed = False
                else:
                    all_passed = False

            m["integrity_status"] = "passed" if all_passed else "failed"
            integrity_results[h] = m["integrity_status"]
        else:
            m["integrity_status"] = "unchecked"

    # 写回 probe_report.json
    with open(PROBE_REPORT, 'w', encoding='utf-8') as f:
        json.dump(probe_report, f, ensure_ascii=False, indent=2)

    # 同步 download_status 到数据库
    sync_download_status_to_db(probe_report)

    # 回写 PDF 路径 + 音频信息到数据库
    _backfill_paths_to_db(probe_report)

    # 同步 integrity_status 到数据库
    if integrity_results:
        batch_update_integrity(integrity_results)
        passed = sum(1 for v in integrity_results.values() if v == "passed")
        failed = sum(1 for v in integrity_results.values() if v == "failed")
        verified_count = len(integrity_results)
        print(f"\n🔍 文件完整性校验：{verified_count} 首诗歌")
        print(f"   完整: {passed} | 损坏: {failed}")
        print("💾 integrity_status 已同步到数据库。")
    else:
        print("\n🔍 文件完整性校验：暂无待校验的诗歌。")


# ================= download_status 更新 =================

def _update_download_status(probe_report):
    """根据本地文件是否存在，更新 probe_report 每条记录的 download_status"""
    from .config import MAP_FILE

    dir_map = {}
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    url = parts[2]
                    h = url.strip('/').split('/')[-1].split('?')[0]
                    dir_map[h] = parts[1]

    for m in probe_report:
        h = m["hymn_number"]
        dir_name = dir_map.get(h)
        if not dir_name:
            m["download_status"] = "dir_missing"
            continue

        dir_path = os.path.join(SAVE_ROOT, dir_name)
        if not os.path.isdir(dir_path):
            m["download_status"] = "dir_missing"
            continue

        # 收集所有该诗歌预期下载的文件路径
        expected_files = []
        if m.get("staff_pdf"):
            expected_files.append(os.path.join(dir_path, f"{h}_五线谱.pdf"))
        if m.get("numbered_pdf"):
            expected_files.append(os.path.join(dir_path, f"{h}_简谱.pdf"))
        for ver, info in m.get("audio_versions", {}).items():
            if "url" in info and info["url"] and not ver.startswith("_"):
                ext = info.get("ext", "m4a")
                expected_files.append(os.path.join(dir_path, f"{h}_{ver}.{ext}"))

        if not expected_files:
            m["download_status"] = "no_files"
            continue

        existing = sum(1 for p in expected_files if os.path.exists(p))

        if existing == len(expected_files):
            m["download_status"] = "completed"
        elif existing == 0:
            m["download_status"] = "failed"
        else:
            m["download_status"] = f"partial({existing}/{len(expected_files)})"

    # 写回 probe_report.json
    with open(PROBE_REPORT, 'w', encoding='utf-8') as f:
        json.dump(probe_report, f, ensure_ascii=False, indent=2)

    # 打印摘要
    status_counts = {}
    for m in probe_report:
        s = m.get("download_status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    print("\n📋 download_status 分布：")
    for s, cnt in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"   {s}: {cnt} ({100*cnt//len(probe_report)}%)")
    print("💾 probe_report.json 已更新。")


# ================= 工具函数 =================

def _find_hymn_dir(hymn_number):
    """根据诗歌编号查找对应的本地目录"""
    map_path = os.path.join(SAVE_ROOT, "url_map.txt")
    if not os.path.exists(map_path):
        return None

    target_name = None
    with open(map_path, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 2:
                url = parts[2]
                h = url.strip('/').split('/')[-1].split('?')[0]
                if h == hymn_number:
                    target_name = parts[1]
                    break

    if target_name:
        dir_path = os.path.join(SAVE_ROOT, target_name)
        if os.path.isdir(dir_path):
            return dir_path

    return None


# ================= 回写数据库 =================

def _backfill_paths_to_db(probe_report):
    """将 probe_report.json 中的 PDF 路径和音频信息回写到数据库
       路径存储为相对于项目根目录（SCRIPT_DIR）的相对路径"""
    import sqlite3

    from .config import DB_PATH, MAP_FILE

    # 计算项目根目录
    SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 构建 dir_map
    dir_map = {}
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    url = parts[2]
                    h = url.strip('/').split('/')[-1].split('?')[0]
                    dir_map[h] = parts[1]

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updated_staff = 0
    updated_num = 0
    updated_audio = 0

    for m in probe_report:
        h = m["hymn_number"]
        dir_name = dir_map.get(h)
        if not dir_name:
            continue

        dir_path = os.path.join(SAVE_ROOT, dir_name)
        staff_rel = num_rel = ""
        staff_file = os.path.join(dir_path, f"{h}_五线谱.pdf")
        num_file = os.path.join(dir_path, f"{h}_简谱.pdf")

        # 存相对路径（相对于项目根目录）
        if m.get("staff_pdf") and os.path.exists(staff_file):
            staff_rel = os.path.relpath(staff_file, SCRIPT_DIR)
        if m.get("numbered_pdf") and os.path.exists(num_file):
            num_rel = os.path.relpath(num_file, SCRIPT_DIR)

        # 构建音频信息（只保留本地文件存在的版本，存相对路径字符串）
        av = m.get("audio_versions", {})
        clean_av = {}
        for ver, info in av.items():
            if not ver.startswith("_"):
                ext = info.get("ext", "m4a") if isinstance(info, dict) else "m4a"
                fp = os.path.join(dir_path, f"{h}_{ver}.{ext}")
                if os.path.exists(fp):
                    clean_av[ver] = os.path.relpath(fp, SCRIPT_DIR)
        audio_json = json.dumps(clean_av, ensure_ascii=False) if clean_av else "{}"
        vl_json = json.dumps(list(clean_av.keys()), ensure_ascii=False)

        c.execute(
            """UPDATE tjc_hymn SET
               staff_img_path = ?,
               numbered_img_path = ?,
               audio_versions = ?,
               audio_version_list = ?,
               updated_at = datetime('now', 'localtime')
               WHERE hymn_number = ?""",
            (staff_rel, num_rel, audio_json, vl_json, h)
        )
        if staff_rel:
            updated_staff += 1
        if num_rel:
            updated_num += 1
        if clean_av:
            updated_audio += 1

    conn.commit()
    conn.close()

    if updated_staff > 0 or updated_num > 0 or updated_audio > 0:
        print(f"📌 数据库已更新：五线谱 {updated_staff} 首 | 简谱 {updated_num} 首 | 音频 {updated_audio} 首")
