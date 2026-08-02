# hymn_crawler/main.py
"""
真耶穌教會聖樂网爬虫 - 主入口文件
统筹调用 step1, step2, step3 等模块
"""

import step1_create_dirs
from step2_extract_text import run_text_extraction
# import step3_download_media # 后续开发

def main():
    print("🚀 真耶穌教會聖樂网爬虫项目启动")
    
    # --- 第一阶段：目录构建 ---
    # 调用 step1 模块的入口函数
    songs_data = step1_create_dirs.run_step1()
    
    # 验证数据是否成功返回
    if songs_data:
        print(f"\n🎉 成功从 Step1 接收到 {len(songs_data)} 首诗歌数据！")
        print("前5首示例:", [song['title'] for song in songs_data[:5]])
        
        # 2. 执行 Step 2: 文本提取与入库
        # 参数: 目标列表, 数据库路径, 是否无头模式(False方便调试)
        result = run_text_extraction(songs_data, db_path="tjc_hymn.db", headless=True)

        if result["success"] > 0:
            print("🎉 Step 2 完成！数据已存入数据库。")
            # 这里可以继续调用 step3_download_files
        else:
            print("⛔ Step 2 未提取到有效数据，请检查网络或网页结构。")
    else:
        print("❌ Step1 执行失败或未获取到数据。")
    
    print("\n🏁 项目流程结束。")

if __name__ == "__main__":
    main()
