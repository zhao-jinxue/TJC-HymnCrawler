# crawler_core/selenium_legacy/driver.py
# Selenium 浏览器驱动管理（保底引擎；懒导入 selenium，避免污染 API 主流程）

SELENIUM_HINT = (
    "未安装 selenium（保底引擎所需）。安装方式："
    "`pip install -r requirements-selenium.txt`，并确保 Chrome 与 chromedriver 版本匹配。"
)


def init_driver():
    """初始化 Chrome 驱动 - 极致加速配置

    - selenium / Chrome / chromedriver 缺失或启动失败时抛出友好异常（带修复指引），
      而非浏览器原始报错，便于用户快速定位环境问题。
    - selenium 在函数内导入：`--engine api` 下 sys.modules 不含 selenium（P0 验收项）。
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError as e:
        raise RuntimeError(SELENIUM_HINT) from e

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.page_load_strategy = 'eager'

    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.stylesheets": 2,
        "profile.default_content_setting_values.fonts": 2,
        "profile.default_content_setting_values.plugins": 2,
        "profile.default_content_setting_values.popups": 2,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--log-level=3")
    options.add_argument("--silent")

    service = Service()
    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        raise RuntimeError(
            "Chrome 浏览器驱动初始化失败，请检查环境："
            "1) 已安装 Google Chrome；"
            "2) chromedriver 与 Chrome 版本匹配（可用 `chromedriver --version` 与 Chrome 版本对比）；"
            "3) 虚拟环境已安装 selenium。"
            f"原始错误: {type(e).__name__}: {e}"
        ) from e
    driver.set_page_load_timeout(8)
    return driver
