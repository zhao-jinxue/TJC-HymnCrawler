# `legacy/` — 保底与历史入口

> 2026-09-13 目录重排：原位于项目根的 `crawler_selenium.py` 移入本目录，
> 使根目录只保留 `README.md` / `crawler_api.py` / `tjc_hymn.db`。

| 文件 | 用途 |
| --- | --- |
| `crawler_selenium.py` | 🛟 Selenium **保底整链入口**：把引擎固定为 `selenium` 后复用 `crawler_api.py` 的菜单/参数，等价 2026-09-12 重构前的行为。脚本自带 `sys.path` 引导，故可直接执行。 |

```bash
# 保底整链（交互菜单，全部走 DOM 引擎；需先装 selenium + Chrome/chromedriver）
/home/zjx/python_env/bin/python legacy/crawler_selenium.py

# 只跑 Step 1（列表页翻页）
/home/zjx/python_env/bin/python legacy/crawler_selenium.py --step 1
```

- 依赖安装：`pip install -r config/requirements-selenium.txt`
- 日常抓取请用 API 主路径：`python crawler_api.py`（零浏览器依赖，Step 1 ≈ 6 s / Step 2 ≈ 60 s）。
- DOM 引擎实现本体在 `crawler_core/selenium_legacy/`（只搬不改，未删一行代码），
  其用途/边界见该目录 README。
