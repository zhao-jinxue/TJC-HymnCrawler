# crawler_core/driver.py
# Selenium 浏览器驱动管理

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


def init_driver():
    """初始化 Chrome 驱动 - 极致加速配置"""
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
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(8)
    return driver
