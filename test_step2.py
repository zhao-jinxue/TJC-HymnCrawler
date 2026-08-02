# hymn_crawler/test_step2.py

import os
import re
import time
import sqlite3
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ================= 配置区 =================
DB_NAME = "tjc_hymn_test.db"

# 测试目标：前 6 首 + 4 首指定的同名诗歌 (共 10 首)
TEST_TARGETS = [
    {"seq_num": "001", "hymn_number": "1", "url": "https://sacredmusic.tjc.org.tw/hymn/1"},
    {"seq_num": "002", "hymn_number": "2", "url": "https://sacredmusic.tjc.org.tw/hymn/2"},
    {"seq_num": "003", "hymn_number": "3", "url": "https://sacredmusic.tjc.org.tw/hymn/3"},
    {"seq_num": "004", "hymn_number": "4", "url": "https://sacredmusic.tjc.org.tw/hymn/4"},
    {"seq_num": "005", "hymn_number": "5", "url": "https://sacredmusic.tjc.org.tw/hymn/5"},
    {"seq_num": "006", "hymn_number": "6", "url": "https://sacredmusic.tjc.org.tw/hymn/6"},
    # 重点测试的同名诗歌
    {"seq_num": "051", "hymn_number": "51_a", "url": "https://sacredmusic.tjc.org.tw/hymn/51_a"},
    {"seq_num": "052", "hymn_number": "51_b", "url": "https://sacredmusic.tjc.org.tw/hymn/51_b"},
    {"seq_num": "125", "hymn_number": "124_a", "url": "https://sacredmusic.tjc.org.tw/hymn/124_a"},
    {"seq_num": "126", "hymn_number": "124_b", "url": "https://sacredmusic.tjc.org.tw/hymn/124_b"},
]

# 简单的简繁判断逻辑
def detect_variant(text):
    # 繁体特有字示例
    trad_chars = set('體國學華教會聖詩讚美')
    # 简体特有字示例
    simp_chars = set('体国学华教会圣诗赞美')
    
    t_count = sum(1 for c in text if c in trad_chars)
    s_count = sum(1 for c in text if c in simp_chars)
    
    if t_count > s_count: return "Traditional"
    if s_count > t_count: return "Simplified"
    return "Mixed/Unknown"

def init_driver():
    """初始化极速版 Chrome 驱动"""
    options = Options()
    # 核心优化：开启无头模式，解决 WSL/Linux 下 GUI 导致的超时
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    # 核心优化：不等待图片/CSS加载，DOM就绪即返回
    options.page_load_strategy = 'eager' 
    
    # 进一步提速：禁止加载图片和样式表
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.stylesheets": 2
    }
    options.add_experimental_option("prefs", prefs)

    try:
        service = Service()  # 自动查找 chromedriver
        driver = webdriver.Chrome(service=service, options=options)
        # 设置全局超时为 15秒，防止无限等待
        driver.set_page_load_timeout(15) 
        return driver
    except Exception as e:
        print(f"❌ 驱动初始化失败: {e}")
        print("提示: 请确保已安装 Chrome 浏览器及对应版本的 chromedriver")
        return None

def parse_hymn_detail(driver, url):
    """解析详情页，适配 Vue 动态结构、Tab 切换及资源提取"""
    result = {
        "title": "Unknown", "lyricist": "Unknown", 
        "composer": "Unknown", "source": "Unknown", "lyrics": "",
        "scores": [], "audios": []  # 新增：乐谱列表 和 音频列表
    }
    
    # 1. 请求页面
    try:
        driver.get(url)
    except TimeoutException:
        pass 
    except Exception as e:
        print(f"   ⚠️ 页面请求异常: {e}")
        return None

    # 2. 【核心】显式等待：死等 .author_name 出现，最多等 10 秒
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".author_name"))
        )
    except TimeoutException:
        print(f"   ❌ 致命错误: 等待 .author_name 超时！")
        # 打印前 500 字符帮助排查
        # print(f"   🔍 调试页面源码片段: {driver.page_source[:500]}")
        return None

    # 3. 基础信息提取
    try:
        author_els = driver.find_elements(By.CSS_SELECTOR, ".author_name")
        if len(author_els) > 0:
            result["lyricist"] = author_els[0].text.strip() or "Unknown"
        if len(author_els) > 1:
            result["composer"] = author_els[1].text.strip() or "Unknown"
    except Exception as e:
        print(f"   ⚠️ 基础信息提取异常: {e}")

    # 4. 詩歌源考提取
    try:
        data_boxes = driver.find_elements(By.CSS_SELECTOR, ".music_data_box")
        for box in data_boxes:
            if "詩歌源考" in box.get_attribute("innerHTML"):
                content_el = box.find_element(By.CSS_SELECTOR, ".content")
                raw_source = content_el.text.strip()
                if raw_source.startswith("詩歌源考"):
                    raw_source = raw_source[4:].strip()
                result["source"] = raw_source
                break
    except Exception as e:
        print(f"   ⚠️ 源考提取异常: {e}")

    # 5. 歌词提取 (处理 Tab 切换)
    lyrics_parts = []
    try:
        # 查找所有的 Tab 按钮
        tabs = driver.find_elements(By.CSS_SELECTOR, ".tab_box .tab")
        
        if not tabs:
            # 如果没有 Tab，说明只有单节歌词，直接提取
            lyric_box = driver.find_element(By.CSS_SELECTOR, ".lyrics_box")
            lyrics_parts.append(lyric_box.text.strip())
        else:
            # 遍历点击每一个 Tab
            for i, tab in enumerate(tabs):
                # 1. 点击 Tab
                tab.click()
                # 2. 等待 Vue 更新 DOM (给一点缓冲时间)
                time.sleep(0.3) 
                
                # 3. 获取当前激活的歌词内容
                # 因为 Vue 可能复用 DOM，我们查找当前可见的 lyrics_box
                active_lyrics = driver.find_elements(By.CSS_SELECTOR, ".lyrics_box")
                current_text = ""
                
                # 尝试获取非空的 lyrics_box
                for box in active_lyrics:
                    text = box.text.strip()
                    if text:
                        current_text = text
                        break
                        
                if current_text:
                    lyrics_parts.append(f"[第{i+1}節]\n{current_text}")
                    
    except Exception as e:
        print(f"   ⚠️ 歌词提取异常: {e}")

    result["lyrics"] = "\n\n".join(lyrics_parts) if lyrics_parts else "Unknown"
    result["title"] = f"聖詩 {result['lyricist']}" if result['lyricist'] != 'Unknown' else "Unknown"

    # 6. 【修复】乐谱链接提取 (基于标题精准定位)
    try:
        # 遍历所有的 music_data_box
        data_boxes = driver.find_elements(By.CSS_SELECTOR, ".music_data_box")
        for box in data_boxes:
            # 1. 检查这个区块的标题是否为"樂譜"
            title_el = box.find_elements(By.CSS_SELECTOR, ".title")
            if title_el and "樂譜" in title_el[0].text:
                # 2. 在当前区块内查找所有的下载链接
                score_links = box.find_elements(By.CSS_SELECTOR, "a.download[href]")
                for link in score_links:
                    href = link.get_attribute("href")
                    # 3. 使用 img.download 的 alt 属性作为文件名
                    alt_img = link.find_elements(By.CSS_SELECTOR, "img.download")
                    alt_name = alt_img[0].get_attribute("alt") if alt_img else "未知乐谱"
                    
                    if href:
                        result["scores"].append({"name": alt_name, "url": href})
                break  # 找到乐谱区块后退出循环
    except Exception as e:
        print(f"   ⚠️ 乐谱提取异常: {e}")

    # 7. 【修复】音频信息提取 (过滤掉无效元素)
    try:
        play_imgs = driver.find_elements(By.CSS_SELECTOR, "img.play")
        for img in play_imgs:
            title = img.get_attribute("data-title")
            audio_type = img.get_attribute("data-type")
            # 过滤掉没有标题的隐藏元素 (如 XML 格式)
            if title and audio_type:
                result["audios"].append({"title": title, "type": audio_type})
    except Exception as e:
        print(f"   ⚠️ 音频提取异常: {e}")

    return result


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tjc_hymn (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seq_num TEXT UNIQUE,
        hymn_number TEXT NOT NULL,
        title TEXT,
        lyricist TEXT,
        composer TEXT,
        source TEXT,
        lyrics TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    return conn

def save_to_db(conn, data):
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO tjc_hymn 
                 (seq_num, hymn_number, title, lyricist, composer, source, lyrics) 
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (data['seq_num'], data['hymn_number'], data['title'], 
               data['lyricist'], data['composer'], data['source'], data['lyrics']))
    conn.commit()

if __name__ == "__main__":
    print("🧪 [Step2 测试] 启动极速版 Selenium 解析引擎...")
    
    driver = init_driver()
    if not driver: exit(1)
    
    conn = init_db()
    
    success_count = 0
    for item in TEST_TARGETS:
        print(f"\n🔍 正在测试: 序号 {item['seq_num']} | 编号: {item['hymn_number']}")
        print(f"   🌐 正在请求: {item['url']}")
        
        start_time = time.time()
        detail = parse_hymn_detail(driver, item['url'])
        cost = time.time() - start_time
        
        if detail:
            item.update(detail)
            # save_to_db(conn, item)  # 暂时注释掉入库，先看控制台输出
            success_count += 1
            print(f"   ✅ 解析成功 (耗时: {cost:.2f}s)")
            print(f"      ✍️ 作词: {item['lyricist']}")
            print(f"      🎵 作曲: {item['composer']}")
            print(f"      📜 源考: {item['source'][:30]}...")
            # print(f"      🎶 完整歌词:\n{item['lyrics']}")  # 完整打印歌词
            # 统计节数
            verse_count = item["lyrics"].count("[第") if item["lyrics"] != "Unknown" else 0
            print(f"      🎶 歌词节数: {verse_count} 节")
            print(f"      📜 完整歌词:\n{item['lyrics']}")

            print(f"      🎼 乐谱数量: {len(item['scores'])}")
            for score in item['scores']:
                print(f"         📄 {score['name']}: {score['url']}")
            print(f"      🎧 音频数量: {len(item['audios'])}")
            for audio in item['audios']:
                print(f"         🎵 {audio['type']}: {audio['title']}")
        else:
            print(f"   ❌ 解析失败")

    print(f"\n🏁 [Step2 测试] 执行完毕。成功: {success_count}/{len(TEST_TARGETS)}")
    driver.quit()
    conn.close()
