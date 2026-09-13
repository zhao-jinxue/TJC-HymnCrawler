# `config/` — 依赖清单与门禁/测试配置

> 2026-09-13 目录重排：为让项目根目录只保留 `README.md` / `crawler_api.py` / `tjc_hymn.db`，
> 原根目录的依赖与工具配置集中到此。**这些文件都在项目根执行**（命令中的路径相对根目录）。

| 文件 | 用途 | 在根目录的执行方式 |
| --- | --- | --- |
| `requirements.txt` | 主依赖（纯 API 路径：requests / urllib3 / beautifulsoup4 / Pillow） | `pip install -r config/requirements.txt` |
| `requirements-selenium.txt` | 保底引擎依赖（selenium；另需 Chrome + chromedriver） | `pip install -r config/requirements-selenium.txt` |
| `pytest.ini` | 测试配置（`selenium` 标记注册） | `python -m pytest -c config/pytest.ini test/ -q` |
| `ruff.toml` | 风格门禁（排除历史独立脚本 `tool/**`、`test/test_step*.py`） | `python -m ruff check --config config/ruff.toml .` |
| `bandit.yaml` | 安全扫描跳过项（B110/B112/B608，均已人工评估） | `python -m bandit -c config/bandit.yaml -r crawler_core crawler_api.py legacy/crawler_selenium.py` |

说明：

- `ruff.toml` / `pytest.ini` 移到子目录后，**必须显式 `--config` / `-c` 指定**，否则工具会回落到默认配置
  （`.clinerules` 与 `README.md` 的命令已同步更新）。
- `requirements-selenium.txt` 内部用 `-r requirements.txt` 相对引用同目录文件，搬动时请两个文件一起搬。
- 完整门禁命令见 `README.md`「开发与门禁」章节。
