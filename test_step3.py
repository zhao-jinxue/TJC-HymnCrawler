# test_step3.py (v6)
# 🧪 第三阶段测试 - 最优资源探测方案
# 策略：
#   乐谱 PDF  → 直接拼接 → 多线程 HEAD（规律固定，全部可用）
#   音频      → 全部多 Selenium 页面点击捕获（类型/格式/URL 均不确定）
#   最终产出完整资源清单 → 合并到 crawler_fast.py 做下载

import os
import re
import time
import json
import sys
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


BASE_URL = "https://sacredmusic.tjc.org.tw"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_ROOT = os.path.join(SCRIPT_DIR, "Hymn_Downloads")
MAP_FILE = os.path.join(SAVE_ROOT, "url_map.txt")


# ========== 工具函数 ==========

def load_url_map():
    songs = []
    if not os.path.exists(MAP_FILE):
        print(f"❌ url_map.txt 不存在: {MAP_FILE}")
        return songs
    with open(MAP_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 3:
                url = parts[2]
                hymn_number = url.strip('/').split('/')[-1].split('?')[0]
                songs.append({
                    "seq_num": parts[0],
                    "hymn_number": hymn_number,
                    "title": parts[1],
                    "url": url
                })
    return songs


def init_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.page_load_strategy = 'eager'
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.stylesheets": 2,
        "profile.default_content_setting_values.fonts": 2,
        "profile.default_content_setting_values.plugins": 2,
        "profile.default_content_setting_values.popups": 2,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--log-level=3")
    options.add_argument("--silent")
    service = Service()
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(8)
    return driver


def head_url_exists(url):
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True, verify=False)
        return resp.status_code == 200
    except Exception:
        return False


# ========== Step A: 并行探测 PDF ==========

def probe_pdfs_parallel(songs):
    print(f"\n📌 [Step A] 多线程验证乐谱 PDF 链接...")
    print(f"    歌曲数: {len(songs)} | 请求数: {len(songs) * 2}")

    def check_staff(h):
        return ("staff", h, f"{BASE_URL}/storage/uploads/hymn/score/sheet/{h}.pdf",
                head_url_exists(f"{BASE_URL}/storage/uploads/hymn/score/sheet/{h}.pdf"))

    def check_num(h):
        return ("num", h, f"{BASE_URL}/storage/uploads/hymn/score/num/{h}.pdf",
                head_url_exists(f"{BASE_URL}/storage/uploads/hymn/score/num/{h}.pdf"))

    start = time.time()
    staff_ok, num_ok = {}, {}
    all_tasks = [check_staff(s["hymn_number"]) for s in songs] + \
                [check_num(s["hymn_number"]) for s in songs]

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(lambda task=task: task): task for task in all_tasks}
        done = 0
        for f in as_completed(futures):
            kind, h, url, ok = f.result()
            if ok:
                if kind == "staff":
                    staff_ok[h] = url
                else:
                    num_ok[h] = url
            done += 1

    elapsed = time.time() - start
    print(f"  ✅ PDF: {len(staff_ok)} 五线谱 + {len(num_ok)} 简谱 | 耗时 {elapsed:.1f}s")

    for s in songs:
        h = s["hymn_number"]
        s["staff_pdf"] = staff_ok.get(h)
        s["numbered_pdf"] = num_ok.get(h)
    return songs


# ========== Step B: 全部 Selenium 页面点击捕获音频 ==========

def capture_song_audio(driver, song):
    """在单个 Selenium 实例中，点击页面所有音频按钮，收集版本→URL 映射"""
    h = song["hymn_number"]
    audio_map = {}

    try:
        driver.get("about:blank")
        driver.get(song["url"])
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#page_banner"))
        )
        time.sleep(1.5)

        # 获取所有播放按钮
        imgs = driver.find_elements(By.CSS_SELECTOR, ".music_data_box .btn img.play")

        for idx in range(len(imgs)):
            cur = driver.find_elements(By.CSS_SELECTOR, ".music_data_box .btn img.play")
            if idx >= len(cur):
                continue
            img = cur[idx]
            dt = img.get_attribute("data-type") or ""

            # 过滤：只处理 data-type 包含"讚美詩"的按钮（排除无 data-type 的干扰项）
            if "讚美詩" not in dt:
                continue

            version = re.sub(r'^讚美詩\s*', '', dt)
            # 标准化版本名：移除可能重复的"四部合唱"前缀
            version = version.replace("四部合唱-", "合唱-")

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

    except Exception as e:
        return (h, {"_error": str(e)[:60]})


def probe_all_audio_parallel(songs, max_workers=4):
    """多 Selenium 实例并行捕获全部诗歌的音频"""
    print(f"\n📌 [Step B] 多 Selenium 并行捕获全部 474 首音频...")
    print(f"    并发数: {max_workers}")

    total = len(songs)
    results = {}
    start = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for s in songs:
            h = s["hymn_number"]
            # 每个任务独立创建和销毁 driver
            def capture_one(song=s):
                driver = init_driver()
                try:
                    return capture_song_audio(driver, song)
                finally:
                    driver.quit()
            futures[pool.submit(capture_one)] = h

        done = 0
        for f in as_completed(futures):
            h, audio_map = f.result()
            results[h] = audio_map
            done += 1
            if done % 10 == 0 or done == total:
                elapsed = time.time() - start
                speed = done / elapsed if elapsed > 0 else 0
                print(f"    ⏳ {done}/{total} | 有音频: {sum(1 for v in results.values() if v and not v.get('_error'))} | "
                      f"耗时 {elapsed:.0f}s | 速度 {speed:.1f}首/s")

    elapsed = time.time() - start
    total_audio = sum(1 for v in results.values() if v and not v.get('_error'))
    print(f"  ✅ 音频探测完成: {total_audio}/{total} 首有音频 | 总耗时 {elapsed:.1f}s")

    # 注入
    for s in songs:
        h = s["hymn_number"]
        s["audio_versions"] = results.get(h, {})

    return songs


# ========== Step C: 生成资源清单 ==========

def generate_manifest(songs):
    manifest = []
    for s in songs:
        manifest.append({
            "hymn_number": s["hymn_number"],
            "title": s["title"],
            "staff_pdf": s.get("staff_pdf"),
            "numbered_pdf": s.get("numbered_pdf"),
            "audio_versions": s.get("audio_versions", {})
        })
    return manifest


def print_summary(manifest):
    staff = sum(1 for m in manifest if m["staff_pdf"])
    num = sum(1 for m in manifest if m["numbered_pdf"])
    audio = sum(1 for m in manifest if m["audio_versions"])

    version_counts = {}
    for m in manifest:
        for ver in m["audio_versions"]:
            version_counts[ver] = version_counts.get(ver, 0) + 1

    print(f"\n{'='*55}")
    print(f"📊 最终资源清单统计")
    print(f"{'='*55}")
    print(f"  总数:    {len(manifest)} 首")
    print(f"  五线谱:  {staff}/{len(manifest)} ({100*staff//len(manifest)}%)")
    print(f"  简谱:    {num}/{len(manifest)} ({100*num//len(manifest)}%)")
    print(f"  有音频:  {audio}/{len(manifest)} ({100*audio//len(manifest)}%)")
    if version_counts:
        print(f"  音频版本分布:")
        for ver, cnt in sorted(version_counts.items(), key=lambda x: -x[1]):
            print(f"    {ver}: {cnt} 首")

    print(f"\n📋 详表（前 10 首）:")
    print(f"{'编号':>6s} | {'诗歌名称':22s} | {'五线谱':>4s} | {'简谱':>4s} | {'音频版本':>10s}")
    print("-" * 55)
    for m in manifest[:10]:
        staff_tag = "✅" if m["staff_pdf"] else "❌"
        num_tag = "✅" if m["numbered_pdf"] else "❌"
        versions = list(m["audio_versions"].keys())
        ver_str = ",".join(versions[:3])
        if len(versions) > 3:
            ver_str += "..."
        title = m["title"][:20].ljust(20)
        print(f" {m['hymn_number']:>6s} | {title} | {staff_tag:>4s} | {num_tag:>4s} | {ver_str:>10s}")


# ========== 主流程 ==========

def main():
    print("=" * 55)
    print("🧪 第三阶段测试 v6 - 最优资源探测")
    print("   PDF: 多线程 HEAD | 音频: 全部 Selenium 点击捕获")
    print("=" * 55)

    songs = load_url_map()
    if not songs:
        print("❌ 无法加载诗歌列表")
        return
    print(f"📂 已加载 {len(songs)} 首诗歌")

    # ---- Step A: PDF 并行探测 ----
    songs = probe_pdfs_parallel(songs)

    # ---- Step B: 全部 Selenium 音频捕获 ----
    songs = probe_all_audio_parallel(songs, max_workers=4)

    # ---- Step C: 资源清单 ----
    manifest = generate_manifest(songs)
    print_summary(manifest)

    report_path = os.path.join(SCRIPT_DIR, "probe_report.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n💾 资源清单已保存: {report_path}")
    print(f"🎉 探测完成！")


if __name__ == "__main__":
    main()
