# crawler_core/extractor.py
# Step 2: 提取诗歌详情页文本

import os
import re
import time
import json
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from .config import BASE_URL, MAP_FILE, SAVE_ROOT
from .driver import init_driver
from .db import save_to_db


class Extractor:

    def __init__(self):
        pass  # 使用 init_driver() 按需创建

    def extract_all(self, songs, driver=None):
        """提取所有诗歌的详情"""
        close_driver = False
        if driver is None:
            driver = init_driver()
            close_driver = True

        total = len(songs)
        print(f"\n📝 Step 2: 提取 {total} 首详情")
        success = 0
        fail = 0
        start = time.time()

        for i, song in enumerate(songs):
            print(f"  [{i+1}/{total}] {song['hymn_number']}...", end="", flush=True)
            data = self._parse_one(driver, song)
            save_to_db(data)

            if data["verse_count"] > 0:
                success += 1
                print(f" ✅ {data['title']} ({data['verse_count']}节)")
            else:
                fail += 1
                print(f" ❌")

            if (i + 1) % 10 == 0:
                elapsed = time.time() - start
                speed = (i + 1) / elapsed
                rem = (total - i - 1) / speed if speed > 0 else 0
                print(f"\n  📈 {i+1}/{total} | {speed:.1f}首/s | 预计剩余 {rem:.0f}s")

        elapsed = time.time() - start
        print(f"\n🏁 Step 2: 成功 {success} | 失败 {fail} | 耗时 {elapsed:.1f}s")

        if close_driver:
            driver.quit()

        return {"success": success, "failed": fail}

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
        except Exception as e:
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
        time.sleep(0.8)
        time.sleep(0.5)

        # 标题
        try:
            title_el = driver.find_elements(By.CSS_SELECTOR, "#page_banner .title")
            if title_el:
                raw = title_el[0].text.strip()
                if raw:
                    clean = re.sub(r'^\d+(?:_[a-zA-Z])?\s*', '', raw)
                    result["title"] = clean if clean else "Unknown"
        except:
            pass

        # 作词/作曲
        try:
            author_els = driver.find_elements(By.CSS_SELECTOR, ".author_name")
            if len(author_els) >= 1:
                result["lyricist"] = author_els[0].text.strip() or "Unknown"
            if len(author_els) >= 2:
                result["composer"] = author_els[1].text.strip() or "Unknown"
        except:
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
                    except:
                        pass
                    content = box.find_element(By.CSS_SELECTOR, ".content")
                    raw = content.text.strip()
                    result["source_info"] = raw[4:].strip() if raw.startswith("詩歌源考") else raw
                    break
        except:
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
                except:
                    pass
            else:
                for tab in tabs:
                    try:
                        tab.click()
                        time.sleep(0.2)
                    except:
                        continue
                    boxes = driver.find_elements(By.CSS_SELECTOR, ".lyrics_box")
                    for box in boxes:
                        text = box.text.strip()
                        if text and text not in lyrics_parts:
                            lyrics_parts.append(text)
                            break
        except:
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
