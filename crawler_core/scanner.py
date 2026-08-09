# crawler_core/scanner.py
# Step 1: 扫描列表页 + 创建目录

import os
import re
import time
from urllib.parse import urljoin

import urllib3
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import BASE_URL, LIST_URL, MAP_FILE, SAVE_ROOT
from .driver import init_driver

urllib3.disable_warnings()


class Scanner:

    def __init__(self):
        self.driver: WebDriver | None = init_driver()
        self.all_songs = []
        self.created_dirs = []
        self.seen_urls = set()
        self.existing_dirs = self._load_existing()

    def _load_existing(self):
        existing = set()
        if os.path.exists(MAP_FILE):
            with open(MAP_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split('|')
                    if len(parts) >= 2:
                        existing.add(parts[1])
        for item in os.listdir(SAVE_ROOT):
            if os.path.isdir(os.path.join(SAVE_ROOT, item)) and item != ".git":
                existing.add(item)
        print(f"✅ 已加载 {len(existing)} 个已有目录")
        return existing

    def scan(self):
        """扫描所有列表页"""
        current_url = LIST_URL
        page_count = 1
        start = time.time()

        print(f"\n{'='*50}")
        print("📋 Step 1: 扫描列表页")
        print(f"{'='*50}")

        driver = self.driver
        if driver is None:
            raise RuntimeError("浏览器驱动未初始化，请检查 Scanner 创建流程")

        while current_url and page_count <= 100:
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

                if full_url in self.seen_urls:
                    continue
                self.seen_urls.add(full_url)

                seq_match = re.match(r'^(.+?)(?:\s|&nbsp;)', raw_title)
                hymn_number = seq_match.group(1) if seq_match else \
                    href.strip('/').split('/')[-1].split('?')[0]

                song = {
                    "seq_num": f"{len(self.all_songs) + 1:03d}",
                    "hymn_number": hymn_number,
                    "title": raw_title,
                    "url": full_url
                }
                self.all_songs.append(song)
                self._create_dir(song)

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
        print(f"\n📊 Step 1 完成: {len(self.all_songs)} 首 | 耗时 {elapsed:.1f}s")
        self._save_map()
        return self.all_songs

    def _create_dir(self, song):
        """创建本地目录"""
        local_seq = f"{len(self.all_songs):03d}"
        safe_name = "".join([c for c in song['title'] if c.isalnum() or c in " _-"]).strip()
        if not safe_name:
            safe_name = "Unknown"

        dir_name_base = f"{local_seq}_{safe_name}"
        final_dir_name = dir_name_base
        dir_path = os.path.join(SAVE_ROOT, final_dir_name)

        if final_dir_name in self.existing_dirs:
            return

        counter = 1
        suffix_map = {i: chr(0x4E00 + i - 1) for i in range(1, 10)}
        while os.path.exists(dir_path):
            suffix = suffix_map.get(counter, str(counter))
            final_dir_name = f"{dir_name_base}-{suffix}"
            dir_path = os.path.join(SAVE_ROOT, final_dir_name)
            counter += 1

        os.makedirs(dir_path, exist_ok=True)
        self.created_dirs.append({
            "id": local_seq,
            "name": final_dir_name,
            "url": song['url']
        })
        self.existing_dirs.add(final_dir_name)

    def _save_map(self):
        """保存 URL 映射表"""
        if not self.created_dirs:
            return
        mode = 'a' if os.path.exists(MAP_FILE) else 'w'
        with open(MAP_FILE, mode, encoding='utf-8') as f:
            f.writelines(f"{item['id']}|{item['name']}|{item['url']}\n" for item in self.created_dirs)
        print(f"💾 新增 {len(self.created_dirs)} 条记录到映射表")

    def close(self):
        """关闭浏览器驱动（幂等：多次调用/驱动已失效时不抛异常）"""
        if self.driver is None:
            return
        try:
            self.driver.quit()
        except Exception:  # noqa: S110, BLE001 - close 为清理操作, 失败不应影响主流程
            pass
        finally:
            self.driver = None
