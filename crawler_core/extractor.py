# crawler_core/extractor.py
# Step 2: 提取诗歌详情（默认 API 直出全部字段；DOM 保底见 selenium_legacy/extractor_dom.py）
#
# v3（2026-09-12 API 重构，§5.3）：
#   - 主路径：官网 API 一次拿全量（列表接口命中 api_cache/），再并发补详情
#     （详情多 prev_no/next_no，写入 DB `api_raw`）→ `api_client.to_db_record()`
#     直出 title/词曲/源考/歌词/副歌，单首 ≈0.2 s，全量 474 首 ≤ 30 s；
#   - 断点续爬沿用 `step2_progress.json`，语义与旧版一致；
#   - 记录级失败（validate_record 报缺 / 编号不匹配 / 详情 404）时：
#     `--engine auto` 或 `USE_SELENIUM_FALLBACK=1` 才降级 DOM，否则登记 failed 不中断其它首；
#   - `group_lyrics_boxes` 保留在此（纯文本归并规则，DOM 兜底与单测共用）。

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import api_client, naming
from .config import (
    API_MAX_WORKERS,
    CRAWL_ENGINE,
    MAP_FILE,
    SAVE_ROOT,
    USE_SELENIUM_FALLBACK,
)
from .db import save_to_db

# 断点进度文件（记录已成功处理的 hymn_number，被 .gitignore 忽略）
PROGRESS_FILE = os.path.join(SAVE_ROOT, "step2_progress.json")

# 进度落盘间隔（首）
PROGRESS_FLUSH_EVERY = 25


def group_lyrics_boxes(tab_boxes):
    """把「每个 Tab 下的 .lyrics_box 文本列表」归并为 (verses, chorus)

    官网 DOM 结构：每个「第N節」Tab 下依次有若干 .lyrics_box——第 1 个是本节歌词，
    其后为副歌（每节重复出现同一段）。判定规则：若某个位置 j（j>=1）在所有 Tab 中
    都存在且文本完全一致，则该位置视为副歌；一旦某位置不一致即停止判定，
    其余 box 视为本节内容（保证不丢文本）。

    Args:
        tab_boxes: [[box_text, ...], ...]，每个元素对应一个 Tab 下的全部 box 文本
    Returns:
        (verses: list[str], chorus: str)
    """
    cleaned = [[t.strip() for t in boxes if t and t.strip()] for boxes in tab_boxes]
    cleaned = [b for b in cleaned if b]
    if not cleaned:
        return [], ""

    # 各 Tab 共同拥有的“正歌之外”的位置数
    common_extra = min(len(b) for b in cleaned) - 1
    chorus_parts = []
    for j in range(1, common_extra + 1):
        first = cleaned[0][j]
        if all(b[j] == first for b in cleaned):
            chorus_parts.append(first)
        else:
            break

    chorus = "\n".join(chorus_parts)
    verses = ["\n".join([boxes[0]] + boxes[1 + len(chorus_parts):]) for boxes in cleaned]
    return verses, chorus


def load_progress():
    """读取断点进度 -> set[hymn_number]；无文件或损坏返回空集"""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("done", []))
    except Exception:  # noqa: BLE001 - 进度文件损坏时从头开始
        return set()


def save_progress(done_set):
    """持久化断点进度"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done_set)}, f, ensure_ascii=False, indent=2)


def clear_progress():
    """删除进度文件（全量重跑用）；不存在时静默"""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


def _normalize_engine(engine):
    """引擎名归一（非法值回落 api）"""
    name = (engine or CRAWL_ENGINE or "api").strip().lower()
    return name if name in ("api", "selenium", "auto") else "api"


class Extractor:

    def __init__(self, engine=None):
        self.engine = _normalize_engine(engine)
        self.driver = None  # 仅 DOM 降级/保底路径按需创建

    # ---------- 入口 ----------

    def extract_all(self, songs, driver=None, resume=True, engine=None, use_cache=None):
        """提取所有诗歌详情（支持断点续爬）

        Args:
            songs: 待提取歌曲列表（来自 `load_url_map()`）
            driver: 可选 Selenium driver（仅 selenium/auto 路径使用；API 路径不会创建浏览器）
            resume: True 时跳过进度文件中已成功的编号；False 全量重抓
            engine: 覆盖 `config.CRAWL_ENGINE`
        Returns:
            {"success": N, "failed": M, "skipped": K}
        """
        engine = _normalize_engine(engine or self.engine)
        done = load_progress() if resume else set()
        pending = [s for s in songs if s["hymn_number"] not in done]
        skipped = len(songs) - len(pending)
        if skipped > 0:
            print(f"\n⏭️ 断点续爬：已跳过 {skipped} 首已成功处理（progress 文件存在）。")
            print(f"   如需全量重跑：删除 {PROGRESS_FILE} 或调用 clear_progress()")

        print(f"\n📝 Step 2: 提取 {len(pending)} 首详情（引擎：{engine}）")
        start = time.time()

        if engine == "selenium":
            success, fail, unresolved = self._extract_legacy(pending, driver)
        else:
            success, fail, unresolved = self._extract_api(pending, use_cache=use_cache)
            if engine == "auto" and unresolved:
                success, fail = self._fallback_dom(unresolved, success, fail, driver)

        elapsed = time.time() - start
        print(f"\n🏁 Step 2: 成功 {success} | 失败 {fail} | 跳过 {skipped} | 耗时 {elapsed:.1f}s")
        return {"success": success, "failed": fail, "skipped": skipped}

    # ---------- API 主路径 ----------

    def _load_records(self, pending, use_cache=None):
        """取记录：列表接口一次拿全量（缓存命中）→ 并发详情补 prev_no/next_no

        详情失败时回退列表记录（列表 == 详情，仅少 prev_no/next_no），保证不因单首抖动丢数据。
        """
        base = {}
        try:
            base = {str(r.get("no") or ""): r for r in
                    api_client.fetch_all(use_cache=use_cache, progress=True)}
        except api_client.ApiError as e:
            print(f"  ⚠️ 列表接口失败（{e}），改为逐首详情兜底")

        nos = [s["hymn_number"] for s in pending]
        details = api_client.fetch_hymns(nos, workers=API_MAX_WORKERS) if nos else {}
        records = {}
        for no in nos:
            rec = details.get(no) or base.get(no)
            if rec:
                records[no] = rec
        return records

    def _extract_api(self, pending, use_cache=None):
        """并发提取（写库在主线程串行，避免 SQLite 写锁竞争）

        Returns:
            (success, fail, unresolved)：unresolved 为记录级失败、可交 DOM 降级的 song 列表
        """
        records = self._load_records(pending, use_cache=use_cache)
        done = load_progress()
        success = 0
        fail = 0
        unresolved = []

        def prepare(song) -> tuple[dict, dict, list]:
            """并发部分：只做 CPU/网络，不碰 DB

            Returns:
                (song, data, problems)：`problems` 非空即失败，此时 `data` 为 `{}`
                （占位、不写库）；调用方先判 `problems`，故下面 `data["verse_count"]`
                的类型恒为 dict，无需再判 None。
            """
            rec = records.get(song["hymn_number"])
            if not rec:
                return song, {}, ["record:not_found"]
            problems = api_client.validate_record(rec)
            if problems:
                return song, {}, problems
            return song, api_client.to_db_record(rec), []

        total = len(pending)
        with ThreadPoolExecutor(max_workers=max(1, API_MAX_WORKERS)) as pool:
            futures = {pool.submit(prepare, s): s for s in pending}
            for i, fut in enumerate(as_completed(futures), 1):
                song = futures[fut]
                try:
                    song, data, problems = fut.result()
                except Exception as e:  # noqa: BLE001 - 单首异常不中断整体（§5.9.2 L2）
                    fail += 1
                    print(f"  [{i}/{total}] #{song['hymn_number']} ⚠️ {type(e).__name__}: {e}")
                    continue

                if problems:
                    fail += 1
                    unresolved.append(song)
                    print(f"  [{i}/{total}] #{song['hymn_number']} ⚠️ 字段异常 {problems}")
                    continue

                try:
                    save_to_db(data)
                except Exception as e:  # noqa: BLE001 - 写库异常登记 failed, 继续其它首
                    fail += 1
                    print(f"  [{i}/{total}] #{song['hymn_number']} ⚠️ 入库失败 {type(e).__name__}: {e}")
                    continue

                if data["verse_count"] > 0:
                    success += 1
                    done.add(song["hymn_number"])
                else:
                    fail += 1
                    print(f"  [{i}/{total}] #{song['hymn_number']} ⚠️ API 无歌词")

                if i % PROGRESS_FLUSH_EVERY == 0 or i == total:
                    save_progress(done)
                    print(f"  📈 {i}/{total} | 成功 {success} | 失败 {fail}")
        save_progress(done)
        return success, fail, unresolved

    # ---------- DOM 保底路径 ----------

    def _ensure_driver(self):
        """按需创建浏览器（仅 DOM 路径；API 路径不会调用）"""
        from .driver import init_driver

        if self.driver is None:
            self.driver = init_driver()
        return self.driver

    def _fallback_dom(self, unresolved, success, fail, driver=None):
        """记录级降级：仅对 API 失败的那几首走 DOM（§4.4 auto 模式）"""
        from .selenium_legacy import selenium_available

        if not selenium_available():
            print(f"  ⚠️ {len(unresolved)} 首待 DOM 降级，但未安装 selenium → 保持 failed。"
                  "（安装保底依赖：pip install -r config/requirements-selenium.txt）")
            return success, fail

        driver = driver or self._ensure_driver()
        print(f"  🔁 {len(unresolved)} 首降级 DOM（保底引擎）...")
        done = load_progress()
        for song in unresolved:
            try:
                data = self._parse_one_dom(driver, song)
                save_to_db(data)
            except Exception as e:  # noqa: BLE001 - 降级失败保持 failed, 不中断其它首
                print(f"  ⚠️ #{song['hymn_number']} DOM 降级失败 {type(e).__name__}: {e}")
                continue
            if data["verse_count"] > 0:
                success += 1
                fail -= 1  # 该首此前计入 failed, 现补回
                done.add(song["hymn_number"])
                save_progress(done)
                print(f"  ✅ #{song['hymn_number']} DOM 降级成功（{data['verse_count']} 节）")
        return success, fail

    def _extract_legacy(self, pending, driver=None):
        """Selenium 保底整链（等价重构前行为）"""
        from .selenium_legacy.driver import SELENIUM_HINT

        try:
            owned = driver is None
            driver = driver or self._ensure_driver()
        except RuntimeError as e:
            print(f"  ❌ {e}")
            raise RuntimeError(SELENIUM_HINT) from e

        done = load_progress()
        success = 0
        fail = 0
        total = len(pending)
        start = time.time()
        try:
            for i, song in enumerate(pending, 1):
                print(f"  [{i}/{total}] {song['hymn_number']}...", end="", flush=True)
                try:
                    data = self._parse_one_dom(driver, song)
                    save_to_db(data)
                except Exception as e:  # noqa: BLE001 - 单首异常不中断整体, 记录后继续
                    fail += 1
                    print(f" ⚠️ {type(e).__name__}: {e}")
                    continue

                if data["verse_count"] > 0:
                    success += 1
                    done.add(song["hymn_number"])
                    save_progress(done)
                    print(f" ✅ {data['title']} ({data['verse_count']}节)")
                else:
                    fail += 1
                    print(" ❌")

                if i % 10 == 0:
                    elapsed = time.time() - start
                    speed = i / elapsed if elapsed > 0 else 0
                    rem = (total - i) / speed if speed > 0 else 0
                    print(f"\n  📈 {i}/{total} | {speed:.1f}首/s | 预计剩余 {rem:.0f}s")
        finally:
            if owned and self.driver is not None:
                self.close()
        return success, fail, []

    # ---------- 单首解析（兼容旧签名） ----------

    def _parse_one(self, driver, song):
        """单首解析：API 主路径；失败且开启降级时走 DOM（原签名保持不变）"""
        allow_dom = USE_SELENIUM_FALLBACK or self.engine in ("selenium", "auto")
        try:
            rec = api_client.fetch_hymn(song["hymn_number"])
            if rec:
                problems = api_client.validate_record(rec)
                if not problems:
                    return api_client.to_db_record(rec)
            else:
                problems = ["record:not_found"]
        except api_client.ApiError as e:
            problems = [f"api_error:{e}"]

        if not allow_dom:
            raise api_client.ApiUnavailableError(
                f"#{song['hymn_number']} API 记录不可用 {problems}"
                "（如需 DOM 降级：--engine auto 或 USE_SELENIUM_FALLBACK=1）")
        return self._parse_one_dom(driver or self._ensure_driver(), song)

    def _parse_one_dom(self, driver, song):
        """DOM 解析（实现见 selenium_legacy/extractor_dom.py，此处仅懒加载转发）"""
        from .selenium_legacy.extractor_dom import parse_one_dom

        return parse_one_dom(driver, song)

    def close(self):
        """关闭浏览器（幂等；API 路径无需调用）"""
        if self.driver is None:
            return
        try:
            self.driver.quit()
        except Exception:  # noqa: S110, BLE001 - 清理失败不影响主流程
            pass
        finally:
            self.driver = None


def load_url_map():
    """从 url_map.txt 加载歌曲列表（seq / hymn_number / title / url）"""
    songs = []
    if not os.path.exists(MAP_FILE):
        return songs
    with open(MAP_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 3:
                url = parts[2]
                h = url.strip('/').split('/')[-1].split('?')[0]
                songs.append({
                    "seq_num": parts[0],
                    "hymn_number": h,
                    "title": parts[1],
                    "url": url
                })
    return songs


__all__ = [
    "PROGRESS_FILE",
    "Extractor",
    "clear_progress",
    "group_lyrics_boxes",
    "load_progress",
    "load_url_map",
    "naming",
    "save_progress",
]
