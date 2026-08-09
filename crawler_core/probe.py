# crawler_core/probe.py
# 资源探测 - PDF (HEAD) + 音频 (Selenium 点击捕获)

import json
import os
import re
import time

import requests
import urllib3

urllib3.disable_warnings()
from concurrent.futures import ThreadPoolExecutor, as_completed

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import BASE_URL, MAP_FILE, PROBE_REPORT


def run_probe(force=False):
    """
    资源探测入口
    force=True: 重新探测;
    force=False: 如果 probe_report.json 存在则跳过
    """
    if os.path.exists(PROBE_REPORT) and not force:
        print(f"✅ probe_report.json 已存在（{os.path.getsize(PROBE_REPORT)//1024}KB）")
        while True:
            try:
                ans = input("  是否重新探测资源？[y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
                print()
            if ans in ("", "n", "no"):
                print("  使用现有 probe_report.json")
                return load_probe_report()
            elif ans in ("y", "yes"):
                print("  开始重新探测...")
                break
            else:
                print("  请输入 Y 或 N")

    return _do_probe()


def load_probe_report():
    """加载已有的探测报告"""
    if not os.path.exists(PROBE_REPORT):
        print("❌ probe_report.json 不存在")
        return []
    with open(PROBE_REPORT, 'r', encoding='utf-8') as f:
        return json.load(f)


def _do_probe():
    """执行完整的资源探测流程"""

    # 加载诗歌列表
    songs = _load_songs()
    if not songs:
        return []

    print(f"📂 已加载 {len(songs)} 首诗歌")

    # Step A: PDF 多线程 HEAD
    songs = _probe_pdfs(songs)

    # Step B: 音频 - 全部 Selenium 页面点击
    songs = _probe_audios(songs)

    # 生成清单
    manifest = [{
        "hymn_number": s["hymn_number"],
        "title": s["title"],
        "staff_pdf": s.get("staff_pdf"),
        "numbered_pdf": s.get("numbered_pdf"),
        "audio_versions": s.get("audio_versions", {})
    } for s in songs]

    # 保存
    with open(PROBE_REPORT, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"💾 资源清单已保存: {PROBE_REPORT}")

    # 打印摘要
    _print_summary(manifest)

    return manifest


def _load_songs():
    """从 url_map.txt 加载诗歌"""
    songs = []
    if not os.path.exists(MAP_FILE):
        print(f"❌ url_map.txt 不存在: {MAP_FILE}")
        return songs
    with open(MAP_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 3:
                url = parts[2]
                h = url.strip('/').split('/')[-1].split('?')[0]
                songs.append({
                    "hymn_number": h,
                    "title": parts[1],
                    "url": url
                })
    return songs


def _probe_pdfs(songs):
    """多线程 PDF 探测"""
    print("\n📌 [PDF] 多线程验证乐谱 PDF...")

    def head_ok(url):
        try:
            return requests.head(url, timeout=5, allow_redirects=True, verify=False).status_code == 200  # nosec B501 - 自有证书环境, 刻意关闭SSL校验
        except Exception:  # noqa: BLE001 - 网络异常视为探测失败
            return False

    staff_ok, num_ok = {}, {}
    tasks = []
    for s in songs:
        h = s["hymn_number"]
        tasks.append(("staff", h, f"{BASE_URL}/storage/uploads/hymn/score/sheet/{h}.pdf"))
        tasks.append(("num", h, f"{BASE_URL}/storage/uploads/hymn/score/num/{h}.pdf"))

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(lambda t=t: (t[0], t[1], t[2], head_ok(t[2]))): t for t in tasks}
        for f in as_completed(futures):
            kind, h, url, ok = f.result()
            if ok:
                if kind == "staff":
                    staff_ok[h] = url
                else:
                    num_ok[h] = url

    print(f"  ✅ PDF: {len(staff_ok)} 五线谱 + {len(num_ok)} 简谱")
    for s in songs:
        h = s["hymn_number"]
        s["staff_pdf"] = staff_ok.get(h)
        s["numbered_pdf"] = num_ok.get(h)
    return songs


def _probe_audios(songs, max_workers=4):
    """多 Selenium 页面点击捕获全部音频"""
    from .driver import init_driver

    print(f"\n📌 [音频] 多 Selenium 并行捕获 {len(songs)} 首...")

    def capture_one(song):
        driver = init_driver()
        try:
            return _capture_song_audio(driver, song)
        finally:
            driver.quit()

    results = {}
    total = len(songs)
    start = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(capture_one, s): s for s in songs}
        done = 0
        for f in as_completed(futures):
            h, amap = f.result()
            results[h] = amap
            done += 1  # noqa: SIM113 - 显式计数器便于进度打印
            if done % 10 == 0 or done == total:
                speed = done / (time.time() - start) if (time.time() - start) > 0 else 0
                print(f"    ⏳ {done}/{total} | 有音频: {sum(1 for v in results.values() if v)} | "
                      f"耗时 {time.time()-start:.0f}s | {speed:.1f}首/s")

    elapsed = time.time() - start
    audio_cnt = sum(1 for v in results.values() if v)
    print(f"  ✅ 音频完成: {audio_cnt}/{total} | 耗时 {elapsed:.1f}s")

    for s in songs:
        h = s["hymn_number"]
        s["audio_versions"] = results.get(h, {})

    return songs


def _capture_song_audio(driver, song):
    """点击页面所有播放按钮，收集音频版本"""
    h = song["hymn_number"]
    audio_map = {}

    try:
        driver.get("about:blank")
        driver.get(song["url"])
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#page_banner"))
        )
        time.sleep(1.5)

        imgs = driver.find_elements(By.CSS_SELECTOR, ".music_data_box .btn img.play")

        for idx in range(len(imgs)):
            cur = driver.find_elements(By.CSS_SELECTOR, ".music_data_box .btn img.play")
            if idx >= len(cur):
                continue
            img = cur[idx]
            dt = img.get_attribute("data-type") or ""

            if "讚美詩" not in dt:
                continue

            version = re.sub(r'^讚美詩\s*', '', dt)

            driver.execute_script("arguments[0].click();", img)
            time.sleep(1.5)

            audio_src = driver.execute_script("""
                const a = document.querySelector('audio#player');
                return a ? (a.src || '') : '';
            """)

            if audio_src and '/audio/' in audio_src:
                fname = audio_src.rstrip('/').split('/')[-1]
                ext = fname.split('.')[-1] if '.' in fname else ''
                audio_map[version] = {
                    "url": audio_src,
                    "filename": fname,
                    "ext": ext
                }

        return (h, audio_map)
    except Exception:  # noqa: BLE001 - 单首音频捕获失败不影响整体
        return (h, {})


def _print_summary(manifest):
    """打印探测报告摘要"""
    total = len(manifest)
    staff = sum(1 for m in manifest if m["staff_pdf"])
    num = sum(1 for m in manifest if m["numbered_pdf"])
    audio = sum(1 for m in manifest if m["audio_versions"])

    vcount = {}
    for m in manifest:
        for v in m["audio_versions"]:
            vcount[v] = vcount.get(v, 0) + 1

    print(f"\n{'='*55}")
    print("📊 资源探测统计")
    print(f"{'='*55}")
    print(f"  总数:    {total} 首")
    print(f"  五线谱:  {staff}/{total} ({100*staff//total}%)")
    print(f"  简谱:    {num}/{total} ({100*num//total}%)")
    print(f"  有音频:  {audio}/{total} ({100*audio//total}%)")
    if vcount:
        print("  音频版本分布:")
        for v, cnt in sorted(vcount.items(), key=lambda x: -x[1]):
            print(f"    {v}: {cnt} ({100*cnt//total}%)")
