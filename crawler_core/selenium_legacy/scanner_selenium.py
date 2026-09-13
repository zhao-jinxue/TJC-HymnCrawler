# crawler_core/selenium_legacy/scanner_selenium.py
# Step 1（保底引擎）：列表页翻页扫描 + DOM 解析 + 建目录 + 写 url_map.txt
#
# 重构前位于 crawler_core/scanner.py::Scanner.scan()，本次原样搬迁（只搬不改），
# 统一由 crawler_core/naming.py 生成目录名（杜绝两套引擎命名漂移）。

import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import naming
from ..config import BASE_URL, LIST_URL

PAGE_LIMIT = 100  # 翻页保护上限


def scan_list_pages(scanner):
    """扫描所有列表页（DOM 引擎）

    Args:
        scanner: `crawler_core.scanner.Scanner` 实例（复用其 driver / _create_dir / _save_map）
    Returns:
        全部 song 列表
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    from .driver import init_driver

    if scanner.driver is None:
        scanner.driver = init_driver()
    driver = scanner.driver

    current_url = LIST_URL
    page_count = 1
    start = time.time()

    print(f"\n{'='*50}")
    print("📋 Step 1: 扫描列表页（Selenium 保底引擎）")
    print(f"{'='*50}")

    while current_url and page_count <= PAGE_LIMIT:
        page_start = time.time()
        print(f"\n--- 第 {page_count} 页 ---")

        try:
            driver.get(current_url)
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.music"))
            )
        except Exception:  # noqa: BLE001 - 等待/导航超时, 继续下一页
            print("  ⚠️ 等待超时，继续...")

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        items = soup.select('div.list > div.music')
        p_elapsed = time.time() - page_start
        print(f"  本页 {len(items)} 首 | 耗时: {p_elapsed:.2f}s")

        if not items:
            break

        for item in items:
            title_tag = item.select_one('div.music_title')
            link_tag = item.select_one('a')
            if not title_tag or not link_tag:
                continue

            raw_title = title_tag.get_text(strip=True)
            href = str(link_tag.get('href') or "")  # bs4 类型桩欠完善, 显式转 str
            full_url = urljoin(BASE_URL, href)

            if full_url in scanner.seen_urls:
                continue
            scanner.seen_urls.add(full_url)

            seq_match = re.match(r'^(.+?)(?:\s|&nbsp;)', raw_title)
            hymn_number = seq_match.group(1) if seq_match else \
                href.strip('/').split('/')[-1].split('?')[0]

            song = {
                "seq_num": naming.seq_from_index(len(scanner.all_songs) + 1),
                "hymn_number": hymn_number,
                "title": raw_title,
                "url": full_url
            }
            # DOM 的 raw_title 已含编号前缀（如「1頌讚獨一真神」）→ 不再重复拼 no
            song["dirname_base"] = naming.to_dirname(song["seq_num"], "", raw_title)
            scanner.all_songs.append(song)
            scanner._create_dir(song)

        # 翻页检测
        pagination_div = soup.find('div', class_='page_box')
        if pagination_div:
            pages = pagination_div.find_all('div', class_='page')
            last_classes = pages[-1].get("class") if pages else None
            if pages and isinstance(last_classes, list) and 'page_enb' in last_classes:
                print("  🔚 最后一页")
                break

        page_count += 1
        if "page=" in current_url:
            current_url = re.sub(r'page=\d+', f'page={page_count}', current_url)
        else:
            current_url = f"{LIST_URL}?page={page_count}"

    elapsed = time.time() - start
    print(f"\n📊 Step 1 完成: {len(scanner.all_songs)} 首 | 耗时 {elapsed:.1f}s")
    scanner._save_map()
    return scanner.all_songs
