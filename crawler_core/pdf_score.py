# crawler_core/pdf_score.py
# 官方简谱 PDF → 「曲谱 + 歌词 + 拍位 + 逐字对应」结构化数据（DB v9 五表的数据源）
#
# 为什么单独成模块（与 pdf_jianpu.py 的分工）：
#   - pdf_jianpu.py：POC。解决「PPT 记号 ↔ PDF 码位」的**跨源映射学习**与人工复核出图。
#   - pdf_score.py：落地。只以**官方 PDF**（网站标准源）为准，把每首谱面拆成
#     「乐句 → 谱行（声部）→ 拍位 → 逐字对位」，产出可直接入库的结构化记录；
#     PPT 仅作旁证（映射表），不再参与逐首对齐。
#
# 实测结构（2026-09-15 取证，见会话日志 2026-09-15_21-09-00.md 任务 3）：
#   - 一首 = 若干「乐句」，每乐句 = 2~4 个**谱层**（各声部，y 相差 ≈24pt）+ 下方一「歌词块」
#   - 歌词块 = 一谱多词的若干行（如 4 节词），行距 ≈15pt，每行字数相同
#   - 谱层元素 x 呈固定栅格（拍位间距 ≈21pt），元素 = 音符字形 / 延长线 / 休止
#   - **等长判据**：主旋律层「去延长线后的元素数」== 每节歌词字数
#     （实测 #334 乐句1：20 元素 − 4 延长线 = 16 == 16 字）
#
# 诚实边界：
#   1. 休止符与音符**形态相同**（都是数字字形），几何无法区分 → 只能靠码位映射；未解码者记 `?`
#      （只影响记号可读性：等长校验用的是「去延长线元素数」，不依赖解码）。
#   2. 作者是按字形宽度手工排版的，逐字对位是**几何候选 + Δ 偏差**（Δ ≤ tol 视为可靠）。
#   3. `count_delta = 音符数 − 字数` 有三种取值：`0` = 一字一音等长；`>0` = 一字多音（正常，
#      `hymn_score_char.span=2` 标出）；`<0` = 音符数不足（硬异常，必须人工复核）。
import os
from dataclasses import dataclass, field

from . import config
from . import pdf_jianpu as P

# 谱层的高度上限（em）：元素墨迹矮于它的是延长线（实测 0.046），高于它的是音符（0.34~0.50）
HOLD_MAX_IH = 0.15
# 乐句分组间距（pt）：同一乐句内相邻谱层 ≈24pt；跨乐句 ≥90pt
PHRASE_GAP = 40.0
# 歌词块分块间距（pt）：块内行距 ≈15pt
LYRIC_GAP = 20.0
# 拍位间距（pt）：实测 ≈21pt（与 pdf_jianpu.BEAT_MIN_GAP 同源）
BEAT_PITCH = 21.0
# 逐字对位容差（pt）
ALIGN_TOL = P.ALIGN_TOL
# 抽取器版本（写入 hymn_score.extractor，便于日后增量重跑）
EXTRACTOR = "pdf_score/1.0"

# 未解码码位的占位记号
UNKNOWN_SYM = "?"

# ================= 记号归一化（PPT 合成字形 → 简谱记号） =================
# 为什么要归一：跨源学习得到的记号沿用 **v8 PPT 体系**——PPT 用「合成字形」表示
# 「数字 + 减时线/八度点」（如 `t` = 5 + 一条减时线），直接入库不便人读。
# 语义依据：`ppt_jianpu.py::NOTE_HEADS` + `简谱字体.ttf` 渲染对照（2026-09-15 目视确认）：
#   q w e r t y u / Q W E R T Y U → 1..7 + 一条减时线（后者再叠高八度点）
#   a d f g h j s / A D F G H J S → 1..7 + 高八度点 + 三条减时线（半宽字形）
# 归一后形如 `1- 2 3- 4`（后缀 `-` = 减时线，`^` = 高八度点，`---` = 三条减时线）；
# **无损的原始码位始终留在 `code_seq`**，任何时刻可重新解码核对。
_SYM_DIGITS = "1234567"
_SYM_ALIASES: dict[str, str] = {
    **{c: f"{d}-" for c, d in zip("qwertyu", _SYM_DIGITS)},
    **{c: f"{d}^-" for c, d in zip("QWERTYU", _SYM_DIGITS)},
    **{c: f"{d}^---" for c, d in zip("adfghjs", _SYM_DIGITS)},
    **{c: f"{d}^---" for c, d in zip("ADFGHJS", _SYM_DIGITS)},
}


def normalize_sym(sym):
    """PPT/PDF 记号 → 简谱记号（未收录者原样返回；`?` 仍表示该码位未解码）"""
    return _SYM_ALIASES.get(sym, sym)


# 人工确认的「码位 → 记号」种子（**目视对齐**，2026-09-15）
#
# 来源与方法：把 #334 首行（`339_334耶穌沙崙玫瑰/334_简谱.pdf` 第 1 乐句主旋律层）的
# **元素码位序列**与渲染出的官方谱逐位对齐——
#   码位：4e56 4ee5 4ee4 4ee5 4e56 4e59 [5d1f] 4e5c 4ef0 4ee4 4ee5 4e56 [5d1f][5d1f]
#         4e53 [5d26] 4ee4 4ee5 4ee8 4e58      （`[...]` 为已剔掉的附点；`5d1f` 是延长线）
#   谱面：3.    2    1    2    3    5    -      6.   5    1    2    3    -    -
#         2.    #    1    2    3    4.
# 与 `pdf_jianpu.py` 既有实测（`4E52`=1 / `4E56`=3 / `4E59`=5 / `4E5C`=6）互相印证。
#
# ⚠️ 同一数字存在**多个字形变体**（如 `4E52` 与 `4EE4` 都渲染成 `1`，区别在减时线/八度点），
# 这里统一归到**音级数字**（用户要的"简谱曲谱"粒度）；要区分修饰可查 `code_seq` + 墨迹尺寸。
# 库里学习到的映射（`hymn_codepoint_map`）**优先级更高**，本表只作兜底。
MANUAL_SEED: dict[int, str] = {
    0x4E52: "1", 0x4E53: "2", 0x4E56: "3", 0x4E58: "4", 0x4E59: "5",
    0x4E5C: "6", 0x4E5D: "7", 0x4E4C: "0",
    0x4EE4: "1", 0x4EE5: "2", 0x4EE8: "3", 0x4EF0: "5",
    0x5D1F: "-", 0x5D26: "#",
}


@dataclass
class ScoreElement:
    """谱行里的一个时值元素（已去空白占位与修饰标记）"""

    index: int          # 行内序号（0 起，与逐字对位的 index 一致）
    cp: int             # PDF 码位
    sym: str            # 解码记号（未解码为 `?`）
    x: float            # 中心 x（pt）
    y: float            # 中心 y（pt）
    iw: float           # 墨迹宽（em）
    ih: float           # 墨迹高（em）
    beat: int = 1       # 起始拍位（1 基）
    span: int = 1       # 占几拍（到下一元素为止）

    @property
    def is_hold(self) -> bool:
        """延长线（`-`）：极矮横线，占时值但不配字"""
        return 0.0 < self.ih <= HOLD_MAX_IH and self.iw <= P.WIDE_LINE_MIN_W

    @property
    def is_rest(self) -> bool:
        """休止符：靠码位映射判定（几何与音符同形，未解码时为 False）"""
        return self.sym in ("0", "00")


@dataclass
class ScoreRow:
    """一个谱层（某声部的一行谱）"""

    row: P.SheetRow                     # 原始谱层
    page: int = 0
    phrase_no: int = 0
    part: str = ""                      # melody / harmony
    elements: list[ScoreElement] = field(default_factory=list)
    x0: float = 0.0

    @property
    def y(self):
        return self.row.y

    @property
    def beat_count(self):
        """拍数：末元素起始拍 + 其 span − 1（延长线/休止都占拍）"""
        if not self.elements:
            return 0
        last = self.elements[-1]
        return last.beat + last.span - 1

    @property
    def core(self):
        """参与配字的元素（去延长线）——与歌词逐字对应的候选序列"""
        return [e for e in self.elements if not e.is_hold]

    def code_seq(self):
        """码位序列（十六进制，空格分隔）——无损，不依赖映射"""
        return " ".join(f"{e.cp:04x}" for e in self.elements)

    def note_seq(self, core_only=True, sep=""):
        """记号序列（解码后）；`?` 表示该码位未学到映射"""
        src = self.core if core_only else self.elements
        return sep.join(e.sym for e in src)

    def __len__(self):
        return len(self.elements)


@dataclass
class ScoreRecord:
    """一首的完整抽取结果（= DB v9 三张表的写入载荷）"""

    hymn_number: str = ""
    pdf_path: str = ""
    page_count: int = 0
    phrase_count: int = 0
    line_count: int = 0
    lyric_count: int = 0
    beat_total: int = 0
    syllable_total: int = 0
    align_ok: int = 0
    review_reason: str = ""
    lines: list[dict] = field(default_factory=list)      # hymn_score_line
    lyrics: list[dict] = field(default_factory=list)     # hymn_score_lyric
    chars: list[dict] = field(default_factory=list)      # hymn_score_char


# ================= 一、乐句 / 谱层 / 歌词块 =================

def is_hold_char(c):
    """单个 PDF 字符是否延长线（极矮横线，占时值但不配字）"""
    return 0.0 < c.ih <= HOLD_MAX_IH and c.iw <= P.WIDE_LINE_MIN_W


def phrase_groups(rows, gap=PHRASE_GAP):
    """谱层 → 乐句组（同页且相邻层 y 间距 ≤ gap 归为同组；实测乐句内 ≈24pt、跨乐句 ≥90pt）"""
    groups: list[list[P.SheetRow]] = []
    for r in rows:
        if groups and groups[-1][-1].page == r.page and r.y - groups[-1][-1].y <= gap:
            groups[-1].append(r)
        else:
            groups.append([r])
    return groups


def lyric_block_for(phrase, lyric_rows, window=P.LYRIC_WINDOW, gap=LYRIC_GAP):
    """乐句 → 其歌词块（谱层最下方一行之下、window 内的歌词行；取最近一块）

    块内只保留「字数 == 众数」的行：谱面上方的标题行/调号行偶尔落进窗口（如 `耶穌沙崙玫瑰`
    6 字 vs 各节 16 字），字数众数判据可把它们摘掉，避免污染等长校验。
    """
    bottom = max(r.y for r in phrase)
    page = phrase[-1].page
    cand = [r for r in lyric_rows
            if r[0].page == page and bottom < r[0].cy <= bottom + window]
    if not cand:
        return []
    block = P.lyric_blocks(cand, gap=gap)[0]
    if len(block) < 2:
        return block
    counts = [len(ln) for ln in block]
    mode = max(set(counts), key=counts.count)
    return [ln for ln in block if len(ln) == mode]


def pick_melody(phrase, block, tol=ALIGN_TOL):
    """乐句组 → 主旋律层

    判据（依次）：①「去延长线元素数」与每节字数的差最小（实测主旋律层差为 0，其它声部差 >0）；
    ② 几何对位超差的字数少；③ y 最小（SATB 记谱里主旋律在最上）。
    """
    syll = len(block[0]) if block else 0
    best = None
    for r in phrase:
        elems = r.elements
        if not elems:
            continue
        core = sum(1 for c in elems if not is_hold_char(c))
        delta = abs(core - syll) if syll else 0
        bad = 0
        if block:
            cells = P.align_lyric(block[0], elems, tol)
            bad = sum(1 for c in cells if c.index < 0 or c.delta > tol)
        key = (delta, bad, r.y)
        if best is None or key < best[0]:
            best = (key, r)
    return best[1] if best else None


# ================= 二、拍位 / 逐字对位 =================

def assign_beats(elements, pitch=BEAT_PITCH):
    """就地写入每个元素的 `beat`（1 基）与 `span`（占几拍）

    拍位 = 首元素第 1 拍，其后按与前一元素的 **x 间距 / pitch** 四舍五入累积（至少 1 拍）。
    取整而非用绝对 x：附点/减时线会让字符宽度不等，间距才是时值的可靠代理。
    """
    prev_x = None
    cur = 1
    for e in elements:
        if prev_x is not None:
            cur += max(1, round((e.x - prev_x) / pitch))
        e.beat = cur
        prev_x = e.x
    for i, e in enumerate(elements):
        nxt = elements[i + 1].beat if i + 1 < len(elements) else e.beat + 1
        e.span = max(1, nxt - e.beat)
    return elements


def align_chars(block, chars, elements, tol=ALIGN_TOL):
    """歌词块首行 ↔ 主旋律层 → 逐字对位记录

    chars：谱层的 **PdfChar** 列表（`P.align_lyric` 要按 `.cx` 做最近邻）
    elements：同层的 `ScoreElement` 列表（按同一序号取解码记号与拍位）
    """
    if not block or not chars:
        return []
    out = []
    for i, c in enumerate(P.align_lyric(block[0], chars, tol), 1):
        e = elements[c.index] if 0 <= c.index < len(elements) else None
        out.append({
            "char_no": i,
            "syllable": c.syllable,
            "note_index": c.index,
            "note": e.sym if e else "",
            "beat": e.beat if e else 0,
            "delta": round(c.delta, 2) if c.index >= 0 else -1.0,
            "span": c.span,
            "align_ok": int(c.index >= 0 and c.delta <= tol),
        })
    return out


# ================= 三、一首的完整抽取 =================

def _rel(path):
    """绝对路径 → 相对项目根的路径（与 tjc_hymn 其它路径字段口径一致）"""
    if not path:
        return ""
    root = getattr(config, "SCRIPT_DIR", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    try:
        return os.path.relpath(path, root)
    except ValueError:          # 跨盘符（Windows）
        return path


def _elements_of(row, mapping):
    """谱层 → [ScoreElement]（含码位解码、记号归一化与拍位写入）"""
    elems = [ScoreElement(index=i, cp=c.cp,
                          sym=normalize_sym(mapping.get(c.cp, UNKNOWN_SYM)),
                          x=c.cx, y=c.cy, iw=c.iw, ih=c.ih)
             for i, c in enumerate(row.elements)]
    return assign_beats(elems)


def build_score(hymn_number, pdf_file=None, mapping=None, tol=ALIGN_TOL, min_elems=8):
    """官方简谱 PDF → ScoreRecord（DB v9 三表的写入载荷）

    mapping：{码位: 记号}（`db.load_codepoint_map()` 或 `P.learn_across` 的产物）；
             缺省时记号列退化为 `?`——拍位/字数/逐字几何对应仍完整可用。
             **人工种子 `MANUAL_SEED` 默认兜底**，库里学到的映射覆盖优先级更高。
    min_elems：谱层最少元素数（乐句谱层实测 14~22；取 8 可滤掉残片层）
    """
    mapping = {**MANUAL_SEED, **(mapping or {})}
    path = pdf_file or P.pdf_path(hymn_number)
    rec = ScoreRecord(hymn_number=str(hymn_number), pdf_path=_rel(path))
    if not path:
        rec.review_reason = "未找到简谱 PDF"
        return rec
    chars = P.read_chars(path)
    if not chars:
        rec.review_reason = "PDF 无文本层（需 OCR，本项目暂不支持）"
        return rec
    rec.page_count = max(c.page for c in chars) + 1
    rows = P.melody_rows(chars, min_elems=min_elems)
    if not rows:
        rec.review_reason = f"未识别到谱层（元素数均 < {min_elems}）"
        return rec
    lyrics = P.lyric_rows(chars)
    groups = phrase_groups(rows)
    rec.phrase_count = len(groups)
    unknown: set[int] = set()
    no_lyric = 0
    short_rows = 0
    line_no = 0
    for pno, phrase in enumerate(groups, 1):
        block = lyric_block_for(phrase, lyrics)
        mel = pick_melody(phrase, block, tol=tol)
        syll = len(block[0]) if block else 0
        mel_no = 0
        for r in phrase:
            line_no += 1
            elems = _elements_of(r, mapping)
            unknown.update(c.cp for c in r.elements if c.cp not in mapping)
            core = [e for e in elems if not e.is_hold]
            rests = sum(1 for e in core if e.is_rest)
            primary = 1 if r is mel else 0
            if primary:
                mel_no = line_no
            cells = align_chars(block, r.elements, elems, tol) if (primary and block) else []
            bad = sum(1 for c in cells if not c["align_ok"])
            # 只有「本乐句有歌词可比」时才判等长：没有歌词块的乐句不参与 Δ 判定（单独记 no_lyric），
            # 否则 `delta = core - 0` 非零，会把「谱面本身无词」误报成「音符数与字数不等」。
            delta = (len(core) - syll) if (primary and syll) else 0
            if primary and syll and (delta < 0 or bad):
                short_rows += 1
            # --- 行记录（谱行表） ---
            rec.lines.append({
                "line_no": line_no,
                "page": r.page,
                "phrase_no": pno,
                "part": "melody" if primary else "harmony",
                "is_primary": primary,
                "y": round(r.y, 2),
                "x0": round(elems[0].x, 2) if elems else 0.0,
                "beat_count": elems[-1].beat + elems[-1].span - 1 if elems else 0,
                "notes": "".join(e.sym for e in elems),
                "notes_core": "".join(e.sym for e in core),
                "code_seq": " ".join(f"{e.cp:04x}" for e in elems),
                "note_count": len(core) - rests,
                "hold_count": len(elems) - len(core),
                "rest_count": rests,
                "syllable_count": syll if primary else 0,
                "count_delta": delta,
                "align_ok": int(primary and syll > 0 and not delta and not bad),
            })
            for c in cells:
                rec.chars.append(dict(c, line_no=line_no))
        if not block:
            no_lyric += 1
        for st, ln in enumerate(block, 1):
            rec.lyrics.append({
                "line_no": mel_no or line_no,
                "stanza_no": st,
                "text": "".join(chr(c.cp) for c in ln),
                "syllable_count": len(ln),
                "align_ok": int(syll > 0 and len(ln) == syll),
            })
    rec.line_count = len(rec.lines)
    rec.lyric_count = len(rec.lyrics)
    rec.beat_total = sum(ln["beat_count"] for ln in rec.lines if ln["is_primary"])
    rec.syllable_total = sum(ln["syllable_count"] for ln in rec.lyrics)
    reasons = []
    if no_lyric:
        reasons.append(f"{no_lyric} 个乐句未找到歌词块")
    if short_rows:
        reasons.append(f"{short_rows} 个乐句音符数不足或对位超差")
    if unknown:
        reasons.append(f"{len(unknown)} 个码位未解码（记号列含 `?`）")
    rec.review_reason = "；".join(reasons)
    # align_ok = 「有歌词可比的乐句」全部通过（无 `音符数不足`、无对位超差）。
    # 两类情况**不计入**：① 谱面本身没有歌词的乐句（no_lyric，只写 review_reason）；
    # ② 码位未解码（只影响记号列可读性，不影响拍位/字数/逐字对位）。
    rec.align_ok = int(not short_rows)
    return rec
