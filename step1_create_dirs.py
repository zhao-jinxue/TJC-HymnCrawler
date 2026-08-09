# hymn_crawler/step1_create_dirs.py

import os
import re
import time
from urllib.parse import urljoin

import urllib3
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# --- 调试配置：关闭不安全请求的警告 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 配置区 =================
BASE_URL = "https://sacredmusic.tjc.org.tw"
LIST_URL = "https://sacredmusic.tjc.org.tw/hymn" 

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL
}

SAVE_ROOT = "Hymn_Downloads"
os.makedirs(SAVE_ROOT, exist_ok=True)

def load_existing_directories():
    """从本地文件系统和映射表中加载已存在的目录名"""
    existing_local_dirs = set()
    map_file = os.path.join(SAVE_ROOT, "url_map.txt")
    
    if os.path.exists(map_file):
        try:
            with open(map_file, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split('|')
                    if len(parts) >= 2:
                        existing_local_dirs.add(parts[1])
            print(f"✅ 已从映射表加载 {len(existing_local_dirs)} 个已有目录记录。")
        except Exception as e:  # noqa: BLE001 - 映射表缺失时降级处理
            print(f"⚠️ 读取映射表失败: {e}")

    try:
        for item in os.listdir(SAVE_ROOT):
            item_path = os.path.join(SAVE_ROOT, item)
            if os.path.isdir(item_path) and item != ".git":
                existing_local_dirs.add(item)
        print(f"✅ 已扫描本地文件夹，当前共记录 {len(existing_local_dirs)} 个目录。")
    except Exception as e:  # noqa: BLE001 - 本地目录扫描异常降级
        print(f"⚠️ 扫描本地目录失败: {e}")
        
    return existing_local_dirs

def get_hymn_list(start_url, existing_dirs):
    """
    使用 Selenium 渲染网页后，提取诗歌信息并创建目录。
    优化了翻页逻辑，并在数据结构中同时保留了本地序号和网页诗歌编号。
    """
    all_songs = []
    current_url = start_url
    page_count = 1
    MAX_PAGES = 100 
    
    print("🚀 开始扫描列表页 (Selenium 模式)...")

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument(f"user-agent={HEADERS['User-Agent']}")

    driver = webdriver.Chrome(options=chrome_options)
    created_dirs = []

    try:
        while current_url and page_count <= MAX_PAGES:
            print(f"\n--- 正在解析第 {page_count} 页: {current_url} ---")
            
            driver.get(current_url)
            
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.music"))
                )
            except Exception:  # noqa: BLE001 - 页面加载超时视为已到末页
                print("⚠️ 页面加载超时，可能已到达最后一页。")
                break

            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            items = soup.select('div.list > div.music')
            print(f"🔍 本页查找到的 'div.music' 元素数量: {len(items)}")

            if not items:
                print("⚠️ 未找到歌曲条目，停止抓取。")
                break

            # --- 处理当前页的数据 ---
            for item in items:
                title_tag = item.select_one('div.music_title')
                link_tag = item.select_one('a')
                
                if title_tag and link_tag:
                    raw_title = title_tag.get_text(strip=True)
                    href = link_tag.get('href')
                    full_url = urljoin(BASE_URL, href)
                    
                    # 1. 提取网页诗歌编号 (例如从 "51_a 萬古靈磐(甲)" 中提取 "51_a")
                    # 使用正则表达式，匹配开头直到遇到空白字符（包括 &nbsp;）为止
                    seq_match = re.match(r'^(.+?)(?:\s|&nbsp;)', raw_title)
                    
                    if seq_match:
                        hymn_number = seq_match.group(1)
                    else:
                        # 保底方案：如果正则匹配失败，尝试从 URL 中提取
                        path = href.strip('/')
                        parts = path.split('/')
                        hymn_id = parts[-1] if parts else "Unknown"
                        hymn_number = hymn_id.split('?')[0]
                    
                    # 2. 去重逻辑 (基于URL)
                    if full_url in [song['url'] for song in all_songs]:
                        continue
                    
                    # 3. 将歌曲信息存入 all_songs，同时包含两个序号
                    all_songs.append({
                        "seq_num": f"{len(all_songs) + 1:03d}",  # 本地连续序号 (001, 002...)
                        "hymn_number": hymn_number,              # 网页真实诗歌编号 (456)
                        "title": raw_title,
                        "url": full_url
                    })
                    print(f"✅ 发现歌曲: {raw_title} {all_songs[-1]}")
                    
                    # 4. 创建目录逻辑 (保留原有的本地序号命名方式)
                    local_seq = f"{len(all_songs):03d}" # 此时 all_songs 已增加一条，len刚好对应
                    safe_name = "".join([c for c in raw_title if c.isalnum() or c in " _-"]).strip()
                    if not safe_name: 
                        safe_name = "Unknown"
                    
                    dir_name_base = f"{local_seq}_{safe_name}"
                    final_dir_name = dir_name_base
                    dir_path = os.path.join(SAVE_ROOT, final_dir_name)

                    if final_dir_name in existing_dirs:
                        print(f"⏭️ 跳过 (已存在): {final_dir_name}")
                        continue

                    # 处理同名 (甲, 乙)
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
                            "id": local_seq,
                            "name": final_dir_name,
                            "url": full_url
                        })
                        existing_dirs.add(final_dir_name)
                        print(f"✅ 已创建: {final_dir_name}")
                    except Exception as e:  # noqa: BLE001 - 单个目录创建失败不中断
                        print(f"❌ 创建目录失败 {final_dir_name}: {e}")

            # --- 翻页逻辑优化：利用 page_enb 判断末页 ---
            pagination_div = soup.find('div', class_='page_box')
            if pagination_div:
                page_elements = pagination_div.find_all('div', class_='page')
                if page_elements:
                    last_page = page_elements[-1]
                    if 'page_enb' in last_page.get('class', []):
                        print("🔚 已解析到最后一页 (检测到 page_enb 标记)。")
                        break
            
            # 构造下一页 URL
            if "page=" in current_url:
                current_url = re.sub(r'page=\d+', f'page={page_count + 1}', current_url)
            else:
                current_url = f"{LIST_URL}?page={page_count + 1}"
                
            page_count += 1
            time.sleep(2)

    except Exception as e:  # noqa: BLE001 - 抓取过程整体出错时打印并收尾
        print(f"❌ 抓取过程出错: {e}")
    finally:
        driver.quit()
        print("\n🛑 浏览器已关闭。")

    return all_songs, created_dirs

def save_map(created_dirs):
    """保存 URL 映射表"""
    if not created_dirs:
        return
        
    url_map_file = os.path.join(SAVE_ROOT, "url_map.txt")
    mode = 'a' if os.path.exists(url_map_file) else 'w' 
    
    try:
        with open(url_map_file, mode, encoding='utf-8') as f:
            f.writelines(f"{item['id']}|{item['name']}|{item['url']}\n" for item in created_dirs)
        print(f"\n💾 新增 {len(created_dirs)} 条记录到映射表。")
    except Exception as e:  # noqa: BLE001 - 保存失败打印错误
        print(f"❌ 保存映射表失败: {e}")

def run_step1():
    """对外暴露的统一入口函数"""
    print("【步骤1】开始执行：创建目录与扫描诗歌...")
    
    existing_local_dirs = load_existing_directories()
    songs, created = get_hymn_list(LIST_URL, existing_local_dirs)
    
    print("\n--- 扫描结果汇总 ---")
    print(f"共发现诗歌: {len(songs)} 首")
    print(f"本次新增目录: {len(created)} 个")
    
    if len(songs) == 0:
        print("❌ 未从网络获取到数据，请检查网络连接或网页结构是否变更。")
    
    print("【步骤1】执行完毕。")
    return songs

if __name__ == "__main__":
    run_step1()
    print("🏁 程序结束。")
