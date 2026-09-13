# crawler_core/selenium_legacy/__init__.py
# Selenium 保底引擎（旧 DOM 实现，只搬不改）
#
# 定位（§4.4）：API 是主路径；本包完整保留重构前的 Selenium 实现作为保底。
#   - 默认不参与任何主流程；`--engine selenium` 或 `crawler_selenium.py` 才会用到；
#   - 本包**不在模块级导入 selenium**（延迟到函数内）→ 未安装 selenium 的机器仍可跑纯 API 全流程；
#   - 需要保底时：`pip install -r requirements-selenium.txt`（另需 Chrome/chromedriver）。
#
# 模块分工：
#   driver.py            WebDriver 工厂（懒导入 selenium）
#   scanner_selenium.py  列表页翻页 + 目录创建（DOM）
#   extractor_dom.py     详情页 DOM 解析（标题/作者/源考/歌词）
#   probe_audio.py       音频「点击播放按钮」捕获（API 无 audio_files 时的兜底）

__all__ = ["selenium_available"]


def selenium_available():
    """selenium 是否已安装（不触发导入，供引擎选择与提示文案使用）"""
    import importlib.util

    return importlib.util.find_spec("selenium") is not None
