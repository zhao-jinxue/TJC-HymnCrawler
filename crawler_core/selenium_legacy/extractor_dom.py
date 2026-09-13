# crawler_core/selenium_legacy/extractor_dom.py
# Step 2（保底引擎）：详情页 DOM 解析（标题 / 作词作曲 / 诗歌源考 / 歌词）
#
# 重构前位于 crawler_core/extractor.py::Extractor._parse_one()，本次原样搬迁（只搬不改），
# 供 `--engine selenium` 与 auto 模式的记录级降级使用。
# selenium 全部延迟导入 → 未装 selenium 时导入本模块不会失败。

import re
import time

from ..extractor import group_lyrics_boxes
from ..lyrics_api import fetch_hymn_lyrics

# DOM 解析超时（秒）：页面框架 / 歌词区
PAGE_READY_TIMEOUT = 12
SECTION_READY_TIMEOUT = 5


def empty_result(song):
    """DOM 解析的初始空结构（键名与 API 路径一致，便于互相替换）"""
    return {
        "hymn_number": song['hymn_number'],
        "title": "Unknown",
        "lyricist": "Unknown",
        "composer": "Unknown",
        "source_info": "",
        "verse_count": 0,
        "verses": [""] * 10,
        "chorus": "",
        "staff_img_path": "",
        "numbered_img_path": "",
        "audio_versions": {}
    }


def _lyrics_ready(driver):
    """歌词区出现非空文本即视为就绪（精确等待替代固定 sleep）"""
    from selenium.webdriver.common.by import By

    els = driver.find_elements(By.CSS_SELECTOR, ".lyrics_box")
    return any((e.text or "").strip() for e in els)


def extract_lyrics_from_dom(driver):
    """从渲染后的 DOM 提炼歌词

    官网每个「第N節」Tab 下依次有多个 .lyrics_box：第 1 个是本节歌词，
    其后为副歌（每节重复同一段）。旧版每个 Tab 只取第一个 box 便 break，
    导致副歌整段丢失——此处改为收集全部 box 后交 group_lyrics_boxes 归并。

    Returns:
        (verses: list[str], chorus: str)
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    tab_boxes = []
    try:
        tabs = driver.find_elements(By.CSS_SELECTOR, ".tab_box .tab")
    except Exception:  # noqa: BLE001 - 无 Tab 结构时退化为整页 box
        tabs = []

    if tabs:
        for tab in tabs:
            try:
                tab.click()
                WebDriverWait(driver, 2).until(_lyrics_ready)
            except Exception:  # noqa: S112, BLE001 nosec B112 - 单个 Tab 点击失败时跳过该 Tab
                continue
            try:
                boxes = driver.find_elements(By.CSS_SELECTOR, ".lyrics_box")
                tab_boxes.append([(b.text or "").strip() for b in boxes])
            except Exception:  # noqa: S110, BLE001 nosec B110 - 歌词区解析失败时跳过该 Tab
                pass
    else:
        try:
            boxes = driver.find_elements(By.CSS_SELECTOR, ".lyrics_box")
            tab_boxes.append([(b.text or "").strip() for b in boxes])
        except Exception:  # noqa: S110, BLE001 nosec B110 - 无歌词框时容错
            pass

    return group_lyrics_boxes(tab_boxes)


def parse_one_dom(driver, song):
    """解析单首诗歌详情页（DOM 引擎，原 `Extractor._parse_one`）"""
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    result = empty_result(song)
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
        WebDriverWait(driver, PAGE_READY_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#page_banner"))
        )
    except TimeoutException:
        return result

    try:
        WebDriverWait(driver, SECTION_READY_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".tab_box, .lyrics_box"))
        )
    except TimeoutException:
        pass
    # 用精确等待替代固定 sleep(0.8)+sleep(0.5)：歌词区域出现非空文本即继续
    try:
        WebDriverWait(driver, SECTION_READY_TIMEOUT).until(_lyrics_ready)
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

    # 歌词：官网 API 为权威源（正歌 lyrics[] + 副歌 lyrics_chorus 分离存放），
    # 失败时才回退 DOM 解析（见 extract_lyrics_from_dom）
    api_lyrics = fetch_hymn_lyrics(song["hymn_number"])
    if api_lyrics["verses"]:
        lyrics_parts = api_lyrics["verses"]
        result["chorus"] = api_lyrics["chorus"]
    else:
        lyrics_parts, chorus = extract_lyrics_from_dom(driver)
        result["chorus"] = chorus

    result["verse_count"] = min(len(lyrics_parts), 10)
    for i in range(min(10, len(lyrics_parts))):
        result["verses"][i] = lyrics_parts[i]

    return result
