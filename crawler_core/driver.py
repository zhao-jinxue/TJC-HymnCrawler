# crawler_core/driver.py
# 转发层：旧 import 路径（`from crawler_core.driver import init_driver`）保持不变
#
# 实现已迁入 `crawler_core/selenium_legacy/driver.py`（Selenium 保底引擎）；
# 本模块只在被调用时才触碰 selenium（legacy 内部延迟导入）→ 纯 API 流程零浏览器依赖。

from .selenium_legacy.driver import SELENIUM_HINT, init_driver

__all__ = ["SELENIUM_HINT", "init_driver"]

