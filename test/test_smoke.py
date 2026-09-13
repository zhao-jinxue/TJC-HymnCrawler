#!/usr/bin/env python3
"""pytest 冒烟测试：覆盖核心纯函数（不依赖网络/Selenium，稳定快速）。

运行: python3 -m pytest test_smoke.py -v
说明: 独立脚本 test_step1/2/3 需要网络与浏览器, 不适合作为 pytest 用例,
      这里提供纯单元级冒烟覆盖, 保障核心逻辑可回归验证。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core.db import resolve_png
from crawler_core.verify import parse_hymn_number_from_path


# ---------- step4: 路径解析编号 ----------
class TestParseHymnNumber:
    def test_normal(self):
        assert parse_hymn_number_from_path(
            "Hymn_Downloads/001_1頌讚獨一真神/1_五线谱.pdf") == "1"

    def test_ab_variant(self):
        assert parse_hymn_number_from_path(
            "Hymn_Downloads/051_51_a萬古靈磐甲/51_a_五线谱.pdf") == "51_a"

    def test_no_dir(self):
        assert parse_hymn_number_from_path("Hymn_Downloads/xxx.pdf") is None


# ---------- step7: PDF 路径 -> PNG 路径推导 ----------
class TestResolvePng:
    def test_single(self):
        # 用实际存在的文件验证(001 简谱 PDF 已转 PNG)
        p = resolve_png("Hymn_Downloads/001_1頌讚獨一真神/1_简谱.pdf")
        assert p and p.endswith("1_简谱.png") and os.path.exists(p)

    def test_none_for_empty(self):
        assert resolve_png("") is None
        assert resolve_png(None) is None


# ---------- step4: 目录列表编号（对纯逻辑的间接覆盖） ----------
class TestDirList:
    def test_ab_suffix_parse(self):
        from crawler_core.verify import list_hymn_dirs
        dirs = list_hymn_dirs()
        # 474 首应有 51_a 这类变体
        assert "51_a" in dirs
        assert len(dirs) >= 470


# ---------- step5: 白边裁剪（trim_to_margin，纯本地临时文件） ----------
class TestTrimToMargin:
    """回归：`gray.point(lambda p: ...)` 改为 256 项查找表后行为不变（pyright 类型告警修复）"""

    @staticmethod
    def _make_png(path, size=(60, 40), content_box=(10, 5, 50, 35)):
        from PIL import Image
        img = Image.new("L", size, 255)
        x0, y0, x1, y1 = content_box
        for x in range(x0, x1):
            for y in range(y0, y1):
                img.putpixel((x, y), 0)
        img.save(str(path))
        return img

    def test_trims_to_content_with_margin(self, tmp_path):
        from crawler_core.images import trim_to_margin
        png = tmp_path / "page.png"
        self._make_png(png)
        original, trimmed = trim_to_margin(str(png), 2)
        assert original == (60, 40)
        # 内容 40×30 + 上下左右各 2 像素边距
        assert trimmed == (44, 34)

    def test_blank_page_not_trimmed(self, tmp_path):
        from PIL import Image

        from crawler_core.images import trim_to_margin
        png = tmp_path / "blank.png"
        Image.new("L", (20, 30), 255).save(str(png))
        original, trimmed = trim_to_margin(str(png), 2)
        assert (original, trimmed) == ((20, 30), (20, 30))