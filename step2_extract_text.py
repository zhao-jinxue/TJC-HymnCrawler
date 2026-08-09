# step2_extract_text.py
import re
import sqlite3
import time

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

"""
    id INTEGER PRIMARY KEY AUTOINCREMENT,                   # ID
    hymn_number TEXT UNIQUE NOT NULL,                       # 诗歌编号
    title TEXT,                                             # 诗歌名称
    lyricist TEXT DEFAULT 'Unknown',                        # 作词者
    composer TEXT DEFAULT 'Unknown',                        # 作曲者
    source_info TEXT,                                       # 诗歌源考
    verse_count INTEGER DEFAULT 0,                          # 歌词总节数
    verse_1 TEXT DEFAULT '',                                # 第01节歌词
    verse_2 TEXT DEFAULT '',                                # 第02节歌词
    verse_3 TEXT DEFAULT '',                                # 第03节歌词
    verse_4 TEXT DEFAULT '',                                # 第04节歌词
    verse_5 TEXT DEFAULT '',                                # 第05节歌词
    verse_6 TEXT DEFAULT '',                                # 第06节歌词
    verse_7 TEXT DEFAULT '',                                # 第07节歌词
    verse_8 TEXT DEFAULT '',                                # 第08节歌词
    verse_9 TEXT DEFAULT '',                                # 第09节歌词
    verse_10 TEXT DEFAULT '',                               # 第10节歌词
    staff_img_path TEXT,                                    # 五线谱图片相对路径
    numbered_img_path TEXT,                                 # 简谱图片相对路径
    piano_audio_path TEXT,                                  # 钢琴音频相对路径
    vocal_audio_path TEXT,                                  # 人声音频相对路径
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP          # 更新时间
"""

class HymnExtractor:
    def __init__(self, db_path="tjc_hymn.db", headless=True):
        """
        初始化赞美诗提取器
        :param db_path: 数据库路径 (默认在当前目录)
        :param headustomer: 是否启用无头模式
        """
        self.db_path = db_path
        self.driver = self._init_driver(headless)
        self._init_db()

    def _init_driver(self, headless):
        """初始化 Chrome 驱动"""
        options = Options()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.page_load_strategy = 'eager'
        
        # 禁止加载图片和CSS以提速
        prefs = {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.stylesheets": 2
        }
        options.add_experimental_option("prefs", prefs)

        service = Service()
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        return driver

    def _init_db(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS tjc_hymn (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hymn_number TEXT UNIQUE NOT NULL,               
                    title TEXT,
                    lyricist TEXT DEFAULT 'Unknown',
                    composer TEXT DEFAULT 'Unknown',
                    source_info TEXT,
                    verse_count INTEGER DEFAULT 0,
                    verse_1 TEXT DEFAULT '',
                    verse_2 TEXT DEFAULT '',
                    verse_3 TEXT DEFAULT '',
                    verse_4 TEXT DEFAULT '',
                    verse_5 TEXT DEFAULT '',
                    verse_6 TEXT DEFAULT '',
                    verse_7 TEXT DEFAULT '',
                    verse_8 TEXT DEFAULT '',
                    verse_9 TEXT DEFAULT '',
                    verse_10 TEXT DEFAULT '',
                    staff_img_path TEXT,
                    numbered_img_path TEXT,
                    piano_audio_path TEXT,
                    vocal_audio_path TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )''')
        conn.commit()
        conn.close()

    def parse_hymn_detail(self, hymn_number, url):
        """
        解析单首诗歌详情
        :param hymn_number: 诗歌编号 (如 1, 51_a)
        :param url: 完整URL
        :return: 包含解析数据的字典
        """
        
        # 【1】初始化干净的默认值
        result = {
            "hymn_number": hymn_number,
            "title": "Unknown",
            "lyricist": "Unknown",
            "composer": "Unknown",
            "source_info": "",
            "verse_count": 0,
            "verses": [""] * 10,  # 预留10节歌词的列表
            "staff_img_path": "",
            "numbered_img_path": "",
            "piano_audio_path": "",
            "vocal_audio_path": ""
        }

        try:
            self.driver.get("about:blank")
            self.driver.get(url)
        except TimeoutException:
            pass
        except Exception as e:  # noqa: BLE001 - 页面请求异常时降级返回空结果
            print(f"   ⚠️ 页面请求异常: {e}")
            return result

        # 等待页面核心元素加载
        try:
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".lyrics_box"))
            )
        except TimeoutException:
            print(f"   ⚠️ 页面加载超时，跳过: {url}")
            return result
        time.sleep(2)

        # 1. 提取诗歌标题
        try:
            # 优先从 Banner 提取，如果没有则从顶部导航提取
            title_el = self.driver.find_elements(By.CSS_SELECTOR, "#page_banner .title")
            if not title_el:
                title_el = self.driver.find_elements(By.CSS_SELECTOR, "#TOP_nav")
            
            if title_el:
                raw_title = title_el[0].text.strip()
                # 升级正则：匹配开头的 数字 + 可选的下划线及字母后缀 + 空白字符
                # 例如 "51_a 萬古靈磐(甲)" -> "萬古靈磐(甲)"
                # 例如 "21 美哉，主耶穌" -> "美哉，主耶穌"
                clean_title = re.sub(r'^\d+(?:_[a-zA-Z])?\s*', '', raw_title)
                result["title"] = clean_title if clean_title else "Unknown"
        except Exception as e:  # noqa: BLE001 - 标题提取失败使用默认值
            print(f"   ⚠️ 标题提取失败: {e}")

        # 2. 提取作词/作曲
        try:
            author_els = self.driver.find_elements(By.CSS_SELECTOR, ".author_name")
            if author_els:
                result["lyricist"] = author_els[0].text.strip() or "Unknown"
            if len(author_els) > 1:
                result["composer"] = author_els[1].text.strip() or "Unknown"
        except Exception as e:  # noqa: BLE001 - 作者提取失败使用默认值
            print(f"   ⚠️ 作者提取失败: {e}")

        # 3. 提取源考 (处理折叠面板)
        try:
            data_boxes = self.driver.find_elements(By.CSS_SELECTOR, ".music_data_box")
            for box in data_boxes:
                inner_html = box.get_attribute("innerHTML")
                if "詩歌源考" in inner_html:
                    # 尝试查找并点击折叠面板的标题，使其展开
                    try:
                        # 常见的折叠面板标题类名可能是 .title, .panel-heading, 或包含“詩歌源考”的元素
                        collapse_trigger = box.find_element(By.CSS_SELECTOR, ".title, .panel-heading, h3, h4")
                        
                        # 检查是否处于折叠状态 (根据实际网页的 class 判断，常见的有 collapsed, closed 等)
                        if "collapsed" in collapse_trigger.get_attribute("class") or \
                           "closed" in collapse_trigger.get_attribute("class"):
                            collapse_trigger.click()
                            time.sleep(0.5) # 等待 Vue 渲染展开的内容
                    except NoSuchElementException:
                        pass # 如果没有折叠按钮，说明本来就是展开的，直接提取即可

                    # 提取内容
                    content_el = box.find_element(By.CSS_SELECTOR, ".content")
                    raw_source = content_el.text.strip()
                    
                    # 清理掉标题文字，只保留正文
                    result["source_info"] = raw_source.replace("詩歌源考", "").strip()
                    break
        except Exception as e:  # noqa: BLE001 - 源考提取失败保持为空
            print(f"   ⚠️ 源考提取失败: {e}")

        # 4. 提取歌词 (处理 Tab)
        lyrics_parts = []
        try:
            tabs = self.driver.find_elements(By.CSS_SELECTOR, ".tab_box .tab")
            if not tabs:
                # 单节
                lyric_box = self.driver.find_element(By.CSS_SELECTOR, ".lyrics_box")
                text = lyric_box.text.strip()
                if text:
                    lyrics_parts.append(text)
            else:
                for tab in tabs:
                    tab.click()
                    time.sleep(0.3) # 等待 Vue 更新
                    boxes = self.driver.find_elements(By.CSS_SELECTOR, ".lyrics_box")
                    for box in boxes:
                        text = box.text.strip()
                        if text and text not in lyrics_parts:
                            lyrics_parts.append(text)
                            break
        except Exception as e:  # noqa: BLE001 - 歌词提取失败时返回空歌词
            print(f"   ⚠️ 歌词提取失败: {e}")

        # 将歌词填入 result["verses"] 列表，并统计节数
        result["verse_count"] = min(len(lyrics_parts), 10)
        for i in range(min(10, len(lyrics_parts))):
            result["verses"][i] = lyrics_parts[i]

        return result

    def save_to_db(self, hymn_data):
        """
        将数据保存到数据库 (UPSERT 逻辑)
        :param hymn_data: parse_hymn_detail 返回的字典
        """
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # 使用 SQLite 3.24+ 的 UPSERT 语法
        sql = '''INSERT INTO tjc_hymn 
                 (hymn_number, title, lyricist, composer, source_info, verse_count, 
                 verse_1, verse_2, verse_3, verse_4, verse_5, 
                 verse_6, verse_7, verse_8, verse_9, verse_10,
                 staff_img_path, numbered_img_path, piano_audio_path, vocal_audio_path)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(hymn_number) DO UPDATE SET
                    title = excluded.title,
                    lyricist = excluded.lyricist,
                    composer = excluded.composer,
                    source_info = excluded.source_info,
                    verse_count = excluded.verse_count,
                    verse_1 = excluded.verse_1,
                    verse_2 = excluded.verse_2,
                    verse_3 = excluded.verse_3,
                    verse_4 = excluded.verse_4,
                    verse_5 = excluded.verse_5,
                    verse_6 = excluded.verse_6,
                    verse_7 = excluded.verse_7,
                    verse_8 = excluded.verse_8,
                    verse_9 = excluded.verse_9,
                    verse_10 = excluded.verse_10,
                    staff_img_path = excluded.staff_img_path,
                    numbered_img_path = excluded.numbered_img_path,
                    piano_audio_path = excluded.piano_audio_path,
                    vocal_audio_path = excluded.vocal_audio_path,
                    updated_at = CURRENT_TIMESTAMP
                '''
                 
        # 构建参数列表
        params = [
            hymn_data["hymn_number"],
            hymn_data["title"],
            hymn_data["lyricist"],
            hymn_data["composer"],
            hymn_data["source_info"],
            hymn_data["verse_count"]
        ]
        
        # 添加歌词节
        for i in range(10):
            params.append(hymn_data["verses"][i])
            
        # 添加路径
        params.extend([
            hymn_data["staff_img_path"],
            hymn_data["numbered_img_path"],
            hymn_data["piano_audio_path"],
            hymn_data["vocal_audio_path"]
        ])

        c.execute(sql, params)
        conn.commit()
        conn.close()

    def close(self):
        """关闭驱动"""
        self.driver.quit()

# -------------------------------------------------
# 供 main.py 调用的封装函数
# -------------------------------------------------

def run_text_extraction(target_list, db_path="tjc_hymn.db", headless=True):
    """
    执行文本提取的主函数，供 main.py 调用
    :param target_list: 目标列表，格式 [{"seq_num": "001", "hymn_number": "1", "url": "..."}, ...]
    :param db_path: 数据库路径
    :param headless: 是否使用无头模式
    :return: 成功/失败统计
    """
    extractor = HymnExtractor(db_path=db_path, headless=headless)
    success_count = 0
    fail_count = 0

    print(f"🚀 开始执行文本提取任务，共 {len(target_list)} 个目标...")

    for item in target_list:
        print(f"  📝 正在处理: {item['hymn_number']} - {item['url']}")
        data = extractor.parse_hymn_detail(item['hymn_number'], item['url'])
        
        # 【核心优化】无论成功与否，都写入数据库，保持 hymn_number 连续占位
        extractor.save_to_db(data)

        if data["verse_count"] > 0:
            success_count += 1
            print(f"   ✅ 成功: {data['hymn_number']} - {data['title']} (歌词{data['verse_count']}节)")
        else:
            fail_count += 1
            print(f"   ❌ 失败: {data['hymn_number']}")

    extractor.close()
    print(f"🏁 文本提取任务结束。成功: {success_count}, 失败: {fail_count}")
    return {"success": success_count, "failed": fail_count}


if __name__ == "__main__":
    TEST_TARGETS = [
        {"seq_num": "051", "hymn_number": "51_a", "url": "https://sacredmusic.tjc.org.tw/hymn/51_a"},
        {"seq_num": "052", "hymn_number": "51_b", "url": "https://sacredmusic.tjc.org.tw/hymn/51_b"},
    ]
    run_text_extraction(TEST_TARGETS, db_path="tjc_hymn.db", headless=True)
    print("🏁 程序结束。")
