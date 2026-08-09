# crawler_core/extractor.py
# Step 2: 提取诗歌详情页文本
# v2: 精确等待替代固定 sleep（提速）+ 断点续爬（progress 文件持久化）

import json
import os
import re
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import MAP_FILE, SAVE_ROOT
from .db import save_to_db
from .driver import init_driver

# 断点进度文件（记录已成功处理的 hymn_number，被 .gitignore 忽略）
PROGRESS_FILE = os.path.join(SAVE_ROOT, "step2_progress.json")


def load_progress():
    """读取断点进度 -> set[hymn_number]；无文件或损坏返回空集"""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("done", []))
    except Exception:  # noqa: BLE001 - 进度文件损坏时从头开始
        return set()


def save_progress(done_set):
    """持久化断点进度"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done_set)}, f, ensure_ascii=False, indent=2)


def clear_progress():
    """删除进度文件（全量重跑用）；不存在时静默"""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


class Extractor:

    def __init__(self):
        pass  # 使用 init_driver() 按需创建

    def extract_all(self, songs, driver=None, resume=True):
        """提取所有诗歌的详情，支持断点续爬。

        Args:
            songs: 待提取歌曲列表（来自 load_url_map()）
            driver: 可选 Selenium driver；None 时内部创建并自动关闭
            resume: True 时跳过进度文件中已成功的编号；False 全量重抓
        Returns:
            {"success": N, "failed": M, "skipped": K}
        """
        close_driver = False
        if driver is None:
            driver = init_driver()
            close_driver = True

        try:
            # 断点续爬：过滤已成功编号
            done = set()
            if resume:
                done = load_progress()

            pending = []
            for s in songs:
                if s["hymn_number"] in done:
                    continue
                pending.append(s)

            total = len(pending)
            skipped = len(songs) - len(pending)
            if skipped > 0:
                print(f"\n⏭️ 断点续爬：已跳过 {skipped} 首已成功处理（progress 文件存在）。")
                print(f"   如需全量重跑：删除 {PROGRESS_FILE} 或调用 clear_progress()")

            print(f"\n📝 Step 2: 提取 {total} 首详情")
            success = 0
            fail = 0
            start = time.time()

            for i, song in enumerate(pending):
                print(f"  [{i+1}/{total}] {song['hymn_number']}...", end="", flush=True)
                try:
                    data = self._parse_one(driver, song)
                    save_to_db(data)
                except Exception as e:  # noqa: BLE001 - 单首异常不中断整体, 记录后继续
                    fail += 1
                    print(f" ⚠️ {type(e).__name__}: {e}")
                    continue

                if data["verse_count"] > 0:
                    success += 1
                    done.add(song["hymn_number"])
                    # 每成功一首立即持久化，保证异常中断后可从下次继续
                    save_progress(done)
                    print(f" ✅ {data['title']} ({data['verse_count']}节)")
                else:
                    fail += 1
                    print(" ❌")

                if (i + 1) % 10 == 0:
                    elapsed = time.time() - start
                    speed = (i + 1) / elapsed
                    rem = (total - i - 1) / speed if speed > 0 else 0
                    print(f"\n  📈 {i+1}/{total} | {speed:.1f}首/s | 预计剩余 {rem:.0f}s")

            elapsed = time.time() - start
            print(f"\n🏁 Step 2: 成功 {success} | 失败 {fail} | 跳过 {skipped} | 耗时 {elapsed:.1f}s")

            return {"success": success, "failed": fail, "skipped": skipped}
        finally:
            if close_driver:
                driver.quit()

    def _parse_one(self, driver, song):
        """解析单首诗歌"""
        result = {
            "hymn_number": song['hymn_number'],
            "title": "Unknown",
            "lyricist": "Unknown",
            "composer": "Unknown",
            "source_info": "",
            "verse_count": 0,
            "verses": [""] * 10,
            "staff_img_path": "",
            "numbered_img_path": "",
            "audio_versions": {}
        }

        url = song['url']

        try:
            driver.get("about:blank")
            driver.get(url)
        except TimeoutException:
            pass
        except Exception as e:  # noqa: BLE001 - 页面访问异常时降级返回空
            print(f" ⚠️ {e}")
            return result

        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#page_banner"))
            )
        except TimeoutException:
            return result

        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".tab_box, .lyrics_box"))
            )
        except TimeoutException:
            pass
        # 用精确等待替代固定 sleep(0.8)+sleep(0.5)：歌词区域出现非空文本即继续
        try:
            def _lyrics_ready(d):
                els = d.find_elements(By.CSS_SELECTOR, ".lyrics_box")
                return any((e.text or "").strip() for e in els)
            WebDriverWait(driver, 5).until(_lyrics_ready)
        except TimeoutException:
            pass

        # 标题
        try:
            title_el = driver.find_elements(By.CSS_SELECTOR, "#page_banner .title")
            if title_el:
                raw = title_el[0].text.strip()
                if raw:
                    clean = re.sub(r'^\d+(?:_[a-zA-Z])?\s*', '', raw)
                    result["title"] = clean if clean else "Unknown"
        except Exception:  # noqa: S110, BLE001 nosec B110 - 元素可能不存在, 容错跳过
            pass

        # 作词/作曲
        try:
            author_els = driver.find_elements(By.CSS_SELECTOR, ".author_name")
            if len(author_els) >= 1:
                result["lyricist"] = author_els[0].text.strip() or "Unknown"
            if len(author_els) >= 2:
                result["composer"] = author_els[1].text.strip() or "Unknown"
        except Exception:  # noqa: S110, BLE001 nosec B110 - 元素可能不存在, 容错跳过
            pass

        # 源考
        try:
            boxes = driver.find_elements(By.CSS_SELECTOR, ".music_data_box")
            for box in boxes:
                inner = box.get_attribute("innerHTML")
                if "詩歌源考" in inner:
                    try:
                        trigger = box.find_element(By.CSS_SELECTOR, ".title, .panel-heading, h3, h4")
                        cls = trigger.get_attribute("class") or ""
                        if "collapsed" in cls or "closed" in cls:
                            trigger.click()
                            time.sleep(0.3)
                    except Exception:  # noqa: S110, BLE001 nosec B110 - 折叠面板可能不可点, 容错跳过
                        pass
                    content = box.find_element(By.CSS_SELECTOR, ".content")
                    raw = content.text.strip()
                    result["source_info"] = raw[4:].strip() if raw.startswith("詩歌源考") else raw
                    break
        except Exception:  # noqa: S110, BLE001 nosec B110 - 源考区块可能不存在, 容错跳过
            pass

        # 歌词
        lyrics_parts = []
        try:
            tabs = driver.find_elements(By.CSS_SELECTOR, ".tab_box .tab")
            if not tabs:
                try:
                    text = driver.find_element(By.CSS_SELECTOR, ".lyrics_box").text.strip()
                    if text:
                        lyrics_parts.append(text)
                except Exception:  # noqa: S110, BLE001 nosec B110 - 无歌词框时容错
                    pass
            else:
                for tab in tabs:
                    try:
                        tab.click()
                        # 用精确等待替代固定 sleep(0.2)：点击后轮询歌词非空即继续
                        WebDriverWait(driver, 2).until(_lyrics_ready)
                    except Exception:  # noqa: S112, BLE001 nosec B112 - 单个 Tab 点击失败时跳过该 Tab
                        continue
                    boxes = driver.find_elements(By.CSS_SELECTOR, ".lyrics_box")
                    for box in boxes:
                        text = box.text.strip()
                        if text and text not in lyrics_parts:
                            lyrics_parts.append(text)
                            break
        except Exception:  # noqa: S110, BLE001 nosec B110 - 歌词区解析失败, 返回空歌词
            pass

        result["verse_count"] = min(len(lyrics_parts), 10)
        for i in range(min(10, len(lyrics_parts))):
            result["verses"][i] = lyrics_parts[i]

        return result


def load_url_map():
    """从 url_map.txt 加载歌曲列表"""
    songs = []
    if not os.path.exists(MAP_FILE):
        return songs
    with open(MAP_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 3:
                url = parts[2]
                h = url.strip('/').split('/')[-1].split('?')[0]
                songs.append({
                    "seq_num": parts[0],
                    "hymn_number": h,
                    "title": parts[1],
                    "url": url
                })
    return songs