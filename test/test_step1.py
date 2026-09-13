import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import time
import re
import urllib3
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# --- 调试配置：关闭不安全请求的警告 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 配置区 =================
BASE_URL = "https://sacredmusic.tjc.org.tw"
# 【关键修改】将列表页 URL 指向真正的 hymn 列表，并带上分页参数
LIST_URL = "https://sacredmusic.tjc.org.tw/hymn" 

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL
}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
SAVE_ROOT = os.path.join(PROJECT_ROOT, "Hymn_Downloads")
os.makedirs(SAVE_ROOT, exist_ok=True)

existing_local_dirs: set[str] = set()


def load_existing_directories():
    map_file = os.path.join(SAVE_ROOT, "url_map.txt")
    if os.path.exists(map_file):
        try:
            with open(map_file, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split('|')
                    if len(parts) >= 2:
                        existing_local_dirs.add(parts[1])
            print(f"✅ 已从映射表加载 {len(existing_local_dirs)} 个已有目录记录。")
        except Exception as e:
            print(f"⚠️ 读取映射表失败: {e}")

    try:
        for item in os.listdir(SAVE_ROOT):
            item_path = os.path.join(SAVE_ROOT, item)
            if os.path.isdir(item_path) and item != ".git":
                existing_local_dirs.add(item)
        print(f"✅ 已扫描本地文件夹，当前共记录 {len(existing_local_dirs)} 个目录。")
    except Exception as e:
        print(f"⚠️ 扫描本地目录失败: {e}")


def get_hymn_list(start_url):
    """
    使用 Selenium 渲染网页后，提取所有诗歌的标题和链接
    """
    all_songs = []
    current_url = start_url
    page_count = 1
    
    print(f"🚀 开始扫描列表页 (Selenium 模式)...")

    # 配置 Chrome 无头模式
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # 无界面模式
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument(f"user-agent={HEADERS['User-Agent']}")

    driver = webdriver.Chrome(options=chrome_options)

    try:
        while current_url and page_count <= 10:
            print(f"\n--- 正在解析第 {page_count} 页: {current_url} ---")
            
            # 1. 让浏览器打开网页
            driver.get(current_url)
            
            # 2. 显式等待：最多等 10 秒，直到 'div.music' 元素出现在页面上
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.music"))
                )
            except Exception as e:
                print("⚠️ 等待页面元素超时，可能页面结构已改变或到达最后一页。")
                break

            # 3. 获取渲染后的完整 HTML 源码
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')

            # 4. 解析歌曲列表
            items = soup.select('div.list > div.music')
            print(f"🔍 BeautifulSoup 查找到的 'div.music' 元素数量: {len(items)}")

            if not items:
                print("⚠️ 未找到任何歌曲条目，停止抓取。")
                break

            for item in items:
                title_tag = item.select_one('div.music_title')
                link_tag = item.select_one('a')
                
                if title_tag and link_tag:
                    title = title_tag.get_text(strip=True)
                    href = link_tag.get('href')
                    full_url = urljoin(BASE_URL, str(href or ""))
                    
                    if full_url not in [song['url'] for song in all_songs]:
                        all_songs.append({"title": title, "url": full_url})
                        print(f"✅ 发现歌曲: {title}")

            # 5. 极简翻页逻辑
            if "page=" in current_url:
                match = re.search(r'page=(\d+)', current_url)
                if match:
                    current_page = int(match.group(1))
                    next_page = current_page + 1
                    current_url = f"{BASE_URL}/hymn?page={next_page}"
                    page_count += 1
                    time.sleep(2) # 浏览器模式下适当增加延迟
                    continue
            
            # 如果没有 page= 参数或无法提取，退出循环
            current_url = None

    except Exception as e:
        print(f"❌ 抓取过程出错: {e}")
    finally:
        # 6. 无论如何都要关闭浏览器，释放内存
        driver.quit()
        print("\n🛑 浏览器已关闭。")

    return all_songs


def create_directories(songs):
    created_dirs = []

    for index, song in enumerate(songs, start=1):
        seq_num = f"{index:03d}"
        original_name = song['title']
        
        safe_name = "".join([c for c in original_name if c.isalnum() or c in " _-"]).strip()
        if not safe_name: safe_name = "Unknown"

        dir_name_base = f"{seq_num}_{safe_name}"
        final_dir_name = dir_name_base
        dir_path = os.path.join(SAVE_ROOT, final_dir_name)

        if final_dir_name in existing_local_dirs:
            print(f"⏭️ 跳过 (已存在): {final_dir_name}")
            continue

        counter = 1
        suffix_map = {i: chr(0x4E00 + i - 1) for i in range(1, 10)} 

        while os.path.exists(dir_path):
            suffix = suffix_map.get(counter, str(counter))
            final_dir_name = f"{dir_name_base}-{suffix}"
            dir_path = os.path.join(SAVE_ROOT, final_dir_name)
            counter += 1

        try:
            os.makedirs(dir_path, exist_ok=True)
            created_dirs.append({
                "id": seq_num,
                "name": final_dir_name,
                "url": song['url']
            })
            existing_local_dirs.add(final_dir_name)
            print(f"✅ 已创建: {final_dir_name}")
        except Exception as e:
            print(f"❌ 创建目录失败 {final_dir_name}: {e}")

    return created_dirs

def save_map(created_dirs):
    if not created_dirs:
        return
        
    url_map_file = os.path.join(SAVE_ROOT, "url_map.txt")
    mode = 'a' if os.path.exists(url_map_file) else 'w' 
    
    try:
        with open(url_map_file, mode, encoding='utf-8') as f:
            for item in created_dirs:
                f.write(f"{item['id']}|{item['name']}|{item['url']}\n")
        print(f"\n💾 新增 {len(created_dirs)} 条记录到映射表。")
    except Exception as e:
        print(f"❌ 保存映射表失败: {e}")

if __name__ == "__main__":
    load_existing_directories()
    songs = get_hymn_list(LIST_URL)
    
    print(f"\n--- 扫描结果汇总 ---")
    print(f"共发现诗歌: {len(songs)} 首")
    
    if len(songs) > 0:
        new_dirs = create_directories(songs)
        save_map(new_dirs)
        print(f"\n🎉 本次新增目录: {len(new_dirs)} 个")
    else:
        print("❌ 未获取到数据。")
    
    print("🏁 程序结束。")
