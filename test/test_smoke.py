#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

from crawler_core.verify import parse_hymn_number_from_path
from crawler_core.db import resolve_png


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