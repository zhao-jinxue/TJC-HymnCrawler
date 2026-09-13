# crawler_core/scanner.py
# Step 1: 扫描列表页 + 创建目录 + 写 url_map.txt
#
# v2（2026-09-12 API 重构）：
#   - 默认 `scan_api()`：48 个请求拿全 474 首（≈5 s），零浏览器依赖；
#   - `scan_legacy()`：Selenium 保底（实现迁至 selenium_legacy/scanner_selenium.py）；
#   - 命名统一走 `naming.py`（双引擎唯一真源），`_create_dir`/`_save_map` 语义不变；
#   - `check_api()`：只比对「API 列表 vs url_map.txt vs 本地目录」三方一致性，不落盘。

import os
import time

from . import api_client, naming
from .config import CRAWL_ENGINE, MAP_FILE, SAVE_ROOT, VALID_ENGINES

# 目录名冲突时的后缀（甲/乙/丙…）
SUFFIX_MAP = {i: chr(0x4E00 + i - 1) for i in range(1, 10)}


class Scanner:

    def __init__(self, engine=None):
        self.engine = _normalize_engine(engine)
        self.driver = None  # 仅 selenium/auto 路径按需创建（API 路径零浏览器依赖）
        self.all_songs = []
        self.created_dirs = []
        self.seen_urls = set()
        self.existing_dirs = self._load_existing()

    # ---------- 引擎分发 ----------

    def scan(self, engine=None, **kwargs):
        """Step 1 入口：按引擎分发（默认 config.CRAWL_ENGINE = api）"""
        engine = _normalize_engine(engine or self.engine)
        if engine == "selenium":
            return self.scan_legacy()
        if engine == "auto":
            try:
                return self.scan_api(**kwargs)
            except Exception as e:  # noqa: BLE001 - API 整体失败时降级保底引擎（§4.4）
                print(f"⚠️ API 扫描失败（{type(e).__name__}: {e}）→ 降级 Selenium 保底")
                return self.scan_legacy()
        return self.scan_api(**kwargs)

    def scan_api(self, use_cache=None, refresh=False):
        """默认路径：官网 API 列表（48 页）→ song 列表 → 建目录 → 写 url_map.txt"""
        print(f"\n{'='*50}")
        print("📋 Step 1: 扫描列表页（官网 API）")
        print(f"{'='*50}")

        start = time.time()
        records = api_client.fetch_all(use_cache=use_cache, refresh=refresh)

        skipped = 0
        for idx, rec in enumerate(records, 1):
            problems = api_client.validate_record(rec)
            if problems:
                skipped += 1
                print(f"  ⚠️ 第 {idx} 条（no={rec.get('no')!r}）字段异常 {problems}，已跳过"
                      f"（需要兜底请用 --engine auto）")
                continue
            song = api_client.to_song(rec, idx)      # seq = 列表位置（不是 no）
            song["dirname_base"] = api_client.to_dirname(idx, rec)
            self.all_songs.append(song)
            self._create_dir(song)

        elapsed = time.time() - start
        print(f"\n📊 Step 1 完成: {len(self.all_songs)} 首 | 跳过 {skipped} | 耗时 {elapsed:.1f}s")
        self._save_map()
        return self.all_songs

    def scan_legacy(self):
        """Selenium 保底路径（实现见 crawler_core/selenium_legacy/scanner_selenium.py）"""
        from .selenium_legacy.scanner_selenium import scan_list_pages

        return scan_list_pages(self)

    # ---------- 一致性校验（--check，不落盘） ----------

    def check_api(self, use_cache=None):
        """三方一致性检查：API 列表 vs url_map.txt vs 本地目录（不写任何文件）

        Returns:
            {"api": n, "map_lines": n, "missing_dir": [...], "map_mismatch": [...],
             "unexpected_map": [...], "ok": bool}
        """
        records = api_client.fetch_all(use_cache=use_cache, progress=False)
        map_entries = _load_map_entries()          # {行号: (id, 目录名, url)}
        map_nos = {url.strip("/").split("/")[-1].split("?")[0] for _, _, url in map_entries.values()}

        expected = {}
        for idx, rec in enumerate(records, 1):
            no = str(rec.get("no") or "")
            expected[no] = {"seq": idx, "dirname": api_client.to_dirname(idx, rec)}

        missing_dir = [no for no, e in expected.items()
                       if e["dirname"] not in self.existing_dirs
                       and not os.path.isdir(os.path.join(SAVE_ROOT, e["dirname"]))]
        map_mismatch = []
        for line_no, (_id, dirname, url) in map_entries.items():
            no = url.strip("/").split("/")[-1].split("?")[0]
            exp = expected.get(no)
            if exp and dirname != exp["dirname"]:
                map_mismatch.append({"line": line_no, "no": no,
                                     "map": dirname, "api_rule": exp["dirname"]})

        result = {
            "api": len(records),
            "map_lines": len(map_entries),
            "missing_dir": missing_dir,
            "map_mismatch": map_mismatch,
            "unexpected_map": sorted(set(expected) - map_nos),
        }
        result["ok"] = not (missing_dir or map_mismatch or result["unexpected_map"])
        return result

    # ---------- 目录 / 映射表（双引擎共用，语义不变） ----------

    def _load_existing(self):
        existing = set()
        if os.path.exists(MAP_FILE):
            with open(MAP_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split('|')
                    if len(parts) >= 2:
                        existing.add(parts[1])
        for item in os.listdir(SAVE_ROOT):
            if os.path.isdir(os.path.join(SAVE_ROOT, item)) and item != ".git":
                existing.add(item)
        print(f"✅ 已加载 {len(existing)} 个已有目录")
        return existing

    def _create_dir(self, song):
        """创建本地目录（历史命名规则不变；同名目录已存在时直接复用，绝不重命名）"""
        seq = song.get("seq_num") or naming.seq_from_index(len(self.all_songs))
        base = song.get("dirname_base") or naming.to_dirname(
            seq, song.get("hymn_number", ""), song.get("title", ""))

        final_dir_name = base
        dir_path = os.path.join(SAVE_ROOT, final_dir_name)

        if final_dir_name in self.existing_dirs:
            return

        counter = 1
        while os.path.exists(dir_path):
            suffix = SUFFIX_MAP.get(counter, str(counter))
            final_dir_name = f"{base}-{suffix}"
            dir_path = os.path.join(SAVE_ROOT, final_dir_name)
            counter += 1

        os.makedirs(dir_path, exist_ok=True)
        self.created_dirs.append({
            "id": seq,
            "name": final_dir_name,
            "url": song['url']
        })
        self.existing_dirs.add(final_dir_name)

    def _save_map(self):
        """保存 URL 映射表（追加模式，格式不变：id|目录名|url）"""
        if not self.created_dirs:
            return
        mode = 'a' if os.path.exists(MAP_FILE) else 'w'
        with open(MAP_FILE, mode, encoding='utf-8') as f:
            f.writelines(f"{item['id']}|{item['name']}|{item['url']}\n" for item in self.created_dirs)
        print(f"💾 新增 {len(self.created_dirs)} 条记录到映射表")

    def close(self):
        """关闭浏览器驱动（幂等：多次调用/驱动已失效时不抛异常）"""
        if self.driver is None:
            return
        try:
            self.driver.quit()
        except Exception:  # noqa: S110, BLE001 - close 为清理操作, 失败不应影响主流程
            pass
        finally:
            self.driver = None


def _normalize_engine(engine):
    """引擎名归一 + 合法性校验（非法值回落 api 并提示）"""
    name = (engine or CRAWL_ENGINE or "api").strip().lower()
    if name not in VALID_ENGINES:
        print(f"⚠️ 未知引擎 {engine!r}，回落 api（可选：{'/'.join(VALID_ENGINES)}）")
        return "api"
    return name


def _load_map_entries():
    """url_map.txt → {行号: (id, 目录名, url)}（缺失返回 {}）"""
    entries = {}
    if not os.path.exists(MAP_FILE):
        return entries
    with open(MAP_FILE, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            parts = line.strip().split('|')
            if len(parts) >= 3:
                entries[i] = (parts[0], parts[1], parts[2])
    return entries


def check_file():
    """`--check` 入口：三方一致性检查（不落盘），返回退出码（0 一致 / 1 有差异）"""
    print("=" * 50)
    print("🔎 Step 1 一致性检查（不落盘）：API 列表 vs url_map.txt vs 本地目录")
    print("=" * 50)
    scanner = Scanner(engine="api")
    result = scanner.check_api()
    print(f"   API 记录: {result['api']} | url_map: {result['map_lines']} 行")
    if result["missing_dir"]:
        print(f"   ⚠️ API 有但本地无目录: {result['missing_dir']}")
    for m in result["map_mismatch"]:
        print(f"   ⚠️ 第 {m['line']} 行 #{m['no']} 目录名与规则不符: {m['map']} ≠ {m['api_rule']}")
    if result["unexpected_map"]:
        print(f"   ⚠️ url_map 有但 API 无: {result['unexpected_map']}")
    print(f"   {'✅ 完全一致' if result['ok'] else '❌ 存在差异'}")
    return 0 if result["ok"] else 1


__all__ = ["Scanner", "check_file"]
