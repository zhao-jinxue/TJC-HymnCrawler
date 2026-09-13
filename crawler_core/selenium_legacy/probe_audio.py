# crawler_core/selenium_legacy/probe_audio.py
# 资源探测（保底引擎）：音频「点击播放按钮」捕获
#
# 重构前位于 crawler_core/probe.py::_capture_song_audio()/_probe_audios()，本次原样搬迁。
# API 主路径下音频 URL 随数据一起返回（0 次点击），本模块仅在两种场景使用：
#   1) `--engine selenium` 整链保底；
#   2) 某首 API 记录缺 audio_files 时的单首兜底。

import re
import time


def capture_song_audio(driver, song):
    """点击页面所有播放按钮，收集音频版本（返回 (编号, {版本: {url, filename, ext}})）"""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

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


def probe_audios_legacy(songs, max_workers=4):
    """多 Selenium 页面点击捕获全部音频（逐首独立 driver，失败仅影响该首）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .driver import init_driver

    print(f"\n📌 [音频] 多 Selenium 并行捕获 {len(songs)} 首（保底引擎）...")

    def capture_one(song):
        driver = init_driver()
        try:
            return capture_song_audio(driver, song)
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
        s["audio_versions"] = results.get(s["hymn_number"], {})

    return songs


def probe_one_audio(song):
    """单首音频兜底（API 无 audio_files 时调用）→ audio_versions dict"""
    from .driver import init_driver

    print(f"  🎧 #{song['hymn_number']} API 无音频清单，回退 Selenium 捕获...")
    driver = init_driver()
    try:
        _, audio_map = capture_song_audio(driver, song)
    finally:
        driver.quit()
    return audio_map
