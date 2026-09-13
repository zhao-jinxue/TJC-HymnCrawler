# `crawler_core/selenium_legacy/` — Selenium 保底引擎

> 结论先说：**API 是主路径，本包是保底**。日常不用管它；只有在「官网内部接口改版」或
> 「API 记录异常需要 DOM 兜底」时才需要它。

## 1. 为什么保留

官网详情页是客户端渲染（Vue），重构前全流程靠 Selenium 驱动浏览器。2026-09-12 起默认改走
官网 JSON API（元数据 + 资源 URL 一次拿全，全量 474 首 ≈ 5–10 s，比 Selenium 快约 10×），
但 API 属于站点**内部接口**、无公开承诺 → 必须留一条可整体替换的旧链作为保底。

本包就是重构前的实现**原样搬迁**（只搬不改）：`driver.py` / `scanner_selenium.py` /
`extractor_dom.py` / `probe_audio.py`。

## 2. 怎么用

```bash
# 1) 安装保底依赖（另外需要 Chrome 与匹配版本的 chromedriver）
pip install -r requirements-selenium.txt

# 2) 整链保底入口（菜单与重构前一致，全部走 DOM 引擎）
/home/zjx/python_env/bin/python crawler_selenium.py

# 3) 或从主入口临时切换引擎
/home/zjx/python_env/bin/python crawler_fast.py --engine selenium --step 1
/home/zjx/python_env/bin/python crawler_fast.py --engine auto --step 2   # API 优先，逐首失败才降级 DOM
```

引擎语义（`--engine`，默认 `api`）：

| 引擎 | 行为 |
| --- | --- |
| `api` | 纯 API，零浏览器依赖（`sys.modules` 不含 selenium） |
| `selenium` | 旧 DOM 引擎整链（等价重构前行为） |
| `auto` | API 优先；某首 `validate_record` 失败 / 编号不匹配 / 无歌词时才对该首降级 DOM |

## 3. 与主流程的边界

- 本包**不在模块级导入 selenium**（延迟到函数内）→ 未装 selenium 的机器仍可跑 API 全流程；
  `--engine api` 下断言 `"selenium" not in sys.modules` 通过。
- 目录名 / 资源文件名统一走 `crawler_core/naming.py`（双引擎唯一命名真源），
  避免历史上「合唱-N部版 / 四部合唱-N部版」这类命名漂移再次产生重复文件。
- 本包**不参与**默认 `pytest`（无浏览器 CI 也能全绿）；需要时用 `-m selenium` 单独运行。

## 4. 什么时候该怀疑保底失效

`init_driver()` 抛 `Chrome 浏览器驱动初始化失败`，通常是 Chrome 升级后 chromedriver 未同步。
保底并非唯一恢复手段：API 路径的失败清单 + 断点续跑（`step2_progress.json`）即可恢复大部分场景。
