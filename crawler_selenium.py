#!/usr/bin/env python3
# crawler_selenium.py
# 🛟 Selenium 保底**独立整链入口**（必备项，§4.4 / P2）
#
# 用途：等价重构前行为——菜单、步骤、DOM 引擎与 2026-09-12 重构之前完全一致。
#   API 是主路径（`crawler_fast.py`），本入口用于「官网内部接口改版」「API 记录异常
#   需要 DOM 兜底」「需要在无 API 场景下复现历史行为」等保底场景。
#
# 依赖：selenium + Chrome + 匹配版本的 chromedriver
#   pip install -r requirements-selenium.txt
#
# 用法（与 crawler_fast.py 完全一致的菜单/参数）：
#   python crawler_selenium.py                     # 交互菜单（全链 DOM 引擎）
#   python crawler_selenium.py --step 1            # 只跑 Step 1（列表页翻页）
#   python crawler_selenium.py --step 7            # 全流程
#
# 说明：本文件不改动任何抓取逻辑，只把引擎固定为 selenium 后复用统一入口
# （`crawler_core/selenium_legacy/` 内为完整旧实现，未删一行代码）。

import os
import sys

from crawler_core.config import VALID_ENGINES

ENGINE = "selenium"


def _require_selenium():
    """校验保底依赖；缺失时给出可执行的修复指引并退出（退出码 3）"""
    from crawler_core.selenium_legacy import selenium_available

    if selenium_available():
        return True
    print("❌ 未安装 selenium，无法运行 Selenium 保底整链。")
    print("   修复：pip install -r requirements-selenium.txt")
    print("   另需安装 Google Chrome，并保证 chromedriver 与 Chrome 版本匹配。")
    print("   提示：日常抓取请直接用 API 主路径 `python crawler_fast.py`（无需浏览器）。")
    return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if ENGINE not in VALID_ENGINES:  # 防御：常量被误改
        print(f"❌ 引擎常量异常：{ENGINE}")
        return 2
    if not _require_selenium():
        return 3

    # 让所有模块（含任何按 config.CRAWL_ENGINE 取默认值的地方）都走 DOM 引擎
    os.environ["CRAWL_ENGINE"] = ENGINE

    from crawler_fast import main as unified_main

    print("🛟 Selenium 保底入口（crawler_selenium.py）：整链 DOM 引擎")
    return unified_main(argv, engine=ENGINE)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断，已保存的部分进度可断点续跑。")
