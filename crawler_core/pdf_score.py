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
import difflib
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
    # 2026-09-16 补充：把 MMP2005 子集字体（从 PDF 提取）里的字形放大到 170px 逐个目视确认。
    # 这几位的字形虽是「数字 + 八度点/减时线」的变体，但数字本体清晰可辨；它们跨源学习
    # 拿不到稳定的票（`5D4C` 更特殊：PPT 把休止符 `0` 当零宽叠加字符剔除，永远没有票），
    # 故按目视入种子。**看不准的一律不加**（如 `4E5E` 字形像 `i`，来源不明 → 继续显示 `?`）。
    0x4E3C: "5",      # 5 + 两个八度点
    0x5D4C: "0",      # 休止符（PPT 侧 `0` 被 ppt_symbols 剔除 → 跨源无票）
    0x5D42: "7",      # 7 + 低八度点
    0x4E43: "6",      # 6 + 低八度点
    0x4E42: "7",      # 7 + 低八度点 + 减时线
    0x4ED9: "7",      # 7 + 低八度点
    0x5D27: "b",      # 降号（字形 `b`；同族的 `5D26` 是升号 `#`）
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


# ================= 五、拍位级跨源学习（突破「整行同构」的长度限制） =================
# 背景（旧法为什么不够）：`pdf_jianpu.align_sequence` 要求「PDF 谱行 ↔ PPT 记号行」**整行 1:1**
#   （尾部元素也只能跳过修饰），但两源行宽不同——PPT 一行 4 小节、官方谱一行 6 小节——
#   实测 #334 第 1 行：PPT 11 记号 vs PDF 20 元素，**前 11 个音级完全一致**，整行却永远对不上。
#
# 做法（拍位级）：
#   1. 两侧都降为**音级序列**（`1`..`7`/`0`）：PDF 侧未解码码位记 None、延长线与线类剔除；
#      PPT 侧走 `ppt_symbols`（剔小节线/叠加修饰）后丢弃非音级记号。粒度与人工种子
#      `MANUAL_SEED` 一致——同一数字的多个字形变体统一归到音级（可读性优先，变体信息
#      仍无损保留在 `hymn_score_line.code_seq` 与字形墨迹尺寸里）。
#   2. `difflib` 求最长公共连续段作**锚点**（PDF 的 None 用位置唯一哨兵占位，保证
#      「未知」不与任何记号相等），再在锚点左右递归找下一个锚点。
#   3. **只在「两个锚点夹出的区间」里投票**，且要求两侧区间**等长**、区间内**已知音级全等**：
#      两个锚点把区间两端钉死，等长即一一对应——对应关系是唯一的，不靠猜。
#      （早期版本让锚点向外「扩展」，实测会把码位投错：留一验证只有 24%，见会话日志。）
#   4. 跨首累计投票，票数与占比双阈值过滤；争议码位（多解）只进 stats 不入库。
ANCHOR_MIN_LEN = 3          # 可信锚点最短长度（音级个数）
ANCHOR_MAX_BLOCKS = 6       # 每对（谱行 × 记号行）最多取几个锚点
ANCHOR_PAIR_MIN = 4         # 一对行至少要有多少音级落在锚点上（否则不是同一句，不投票）
PAIR_COV_MIN = 0.6          # 行对锚点覆盖率下限：真对应 ≈0.82~1.0，凑巧撞上的 ≤0.6
ANCHOR_MIN_VOTES = 3        # 码位成为候选的最少票数
ANCHOR_MIN_RATIO = 0.7      # 最高票占比下限（低于它判为争议，不入库）
LEARN_MARK_MAX_H = 0.30     # 未解码元素「又矮又窄」判为修饰的墨迹高上限（em）
LEARN_MARK_MAX_W = 0.16     # 同上的墨迹宽上限：升降号 ≈0.25×0.11、附点 ≈0.07×0.07，音符 ≥0.34 高
LINE_MARK_MAX_W = 0.06      # 极窄字形（宽 < 0.06em）判为线类：细小节线 `601d` 实测 ≈0.04

_GRADE_CHARS = "01234567"
_SENTINEL = "\x00unknown"


def grade_of(sym):
    """记号 → 音级（`1`..`7`/`0`）；非音级（`?`、升号、延长线、线类）返回 None"""
    if not sym or sym == UNKNOWN_SYM:
        return None
    for ch in normalize_sym(sym):
        if ch in _GRADE_CHARS:
            return ch
    return None


def grade_seq(symbols):
    """[记号] → [音级 | None]"""
    return [grade_of(s) for s in symbols]


def ppt_grade_seq(notes):
    """PPT `notes` → (参与比对的记号, 音级)（两侧都只保留能定音级的记号）"""
    pairs = [(normalize_sym(s), grade_of(s)) for s in P.ppt_symbols(notes or "")]
    pairs = [(s, g) for s, g in pairs if g is not None]
    return [s for s, _ in pairs], [g for _, g in pairs]


def anchor_blocks(pdf_grades, ppt_grades, min_len=ANCHOR_MIN_LEN, max_blocks=ANCHOR_MAX_BLOCKS):
    """两侧音级序列的「最长公共连续段」锚点（递归取最长）→ [(pdf_i, ppt_j, n), ...] 保序

    PDF 侧 None（未解码码位）替换为**位置唯一哨兵**：它参与长度占位，却不与任何 PPT 记号
    相等，避免「未知 ↔ 未知」互相匹配把短巧合撑成锚点。
    """
    a = [g if g is not None else f"{_SENTINEL}{i}" for i, g in enumerate(pdf_grades)]
    b = list(ppt_grades)
    out: list[tuple[int, int, int]] = []

    def rec(i0, i1, j0, j1):
        if len(out) >= max_blocks or i1 - i0 < min_len or j1 - j0 < min_len:
            return
        matcher = difflib.SequenceMatcher(None, a[i0:i1], b[j0:j1], autojunk=False)
        i, j, n = matcher.find_longest_match(0, i1 - i0, 0, j1 - j0)
        if n < min_len:
            return
        out.append((i0 + i, j0 + j, n))
        rec(i0, i0 + i, j0, j0 + j)
        rec(i0 + i + n, i1, j0 + j + n, j1)

    rec(0, len(a), 0, len(b))
    return sorted(out)


def learn_elements(row, mapping, min_gap=P.BEAT_MIN_GAP):
    """学习用元素序列：只留「占拍位、可定音级」的元素

    为什么要挑：PPT 侧的拍位序列只含时值记号，而 PDF 侧同一行还夹着**不占拍位**的东西，
    留着会让两侧位置错开、把修饰投成音级（实测：种子里的升号 `5d26` 被投成 `4`）。
    四类一律剔除：
      ① 延长线（`is_hold`）：PPT 用合成字形/叠加字符表示（`5/`、`t`），不独立占位；
      ② 线类（`ih > ROW_MAX_IH`，小节线/终止线）：PPT 侧本就剔除；
      ③ **已知但非音级**的记号（`#`/`|`…）：PPT 侧是零宽叠加修饰，不占位；
      ④ 与前一元素间距 < `min_gap` 的**未解码**码位：紧贴修饰（附点/升降号/减时线），
         不是独立拍位（与 `pdf_jianpu.align_sequence` 的同款几何判据）；
      ⑤ 又矮又窄的**未解码**字形（`ih < LEARN_MARK_MAX_H` 且 `iw < LEARN_MARK_MAX_W`）：
         升降号/附点等不占时值的修饰——它们若离得远（间距 ≥ min_gap）就会漏过第 ④ 条，
         实测把降号 `5d27` 投成了音级 `5`。
    第 ④⑤ 条只针对「未解码」是必要的：已解码的紧贴元素（如 #14 的 `-` 组）是真实拍位，
    不能因为几何近就丢掉。
    """
    out = []
    prev_x = None
    for e in _elements_of(row, mapping):
        gap = None if prev_x is None else e.x - prev_x
        prev_x = e.x
        if e.is_hold or e.ih > P.ROW_MAX_IH:
            continue
        known = e.sym != UNKNOWN_SYM
        grade = grade_of(e.sym)
        if known and grade is None:
            continue
        if not known:
            if gap is not None and gap < min_gap:
                continue
            if 0.0 < e.ih < LEARN_MARK_MAX_H and 0.0 < e.iw < LEARN_MARK_MAX_W:
                continue
        out.append(e)
    return out


def _gap_votes(pdf_elems, pdf_grades, ppt_grades, lo, hi, jlo, jhi):
    """`pdf[lo:hi]` × `ppt[jlo:jhi]` 段的投票（要求等长、区间内已知音级逐一相等）

    不满足即返回 []（该段不可信：说明中间还夹着别的东西——未解码的紧贴修饰、PPT 多写/
    漏写的小节等，对应关系不再是 1:1）。
    """
    if hi - lo <= 0 or hi - lo != jhi - jlo:
        return []
    votes: list[tuple[int, str]] = []
    for t in range(hi - lo):
        grade = pdf_grades[lo + t]
        if grade is None:
            votes.append((pdf_elems[lo + t].cp, ppt_grades[jlo + t]))
        elif grade != ppt_grades[jlo + t]:
            return []
    return votes


def votes_from_pair(pdf_elems, pdf_grades, ppt_grades, min_len=ANCHOR_MIN_LEN,
                    pair_min=ANCHOR_PAIR_MIN, cov_min=PAIR_COV_MIN):
    """一对（PDF 谱行 × PPT 记号行）→ [(码位, 音级), ...]

    闸门与投票规则（每条都是被实测踩出来的，见会话日志）：
      ① **覆盖率闸门**：锚点覆盖的音级数 / min(两侧音级数) ≥ `cov_min`。实测真对应的行对
         ≈0.82~1.0（如 #334 `32123565123` ↔ PDF `3212356512321234` 为 1.0），而只是凑巧撞上
         几个音级的假对应 ≤0.6（多数是 0）——这条把「两个不同的句子」挡在门外，是准确率的关键。
      ② 锚点内的位置是「已解码音级 == PPT 音级」，无需投票。
      ③ 只在「锚点 ↔ 相邻锚点」之间、以及「锚点 ↔ 该侧下一个**已知**音级」之间的区间投票，
         且必须**两侧等长**、区间内已知音级**逐一相等**。
      ④ 第 ③ 条里用「下一个已知音级」当**端点验证**：先按 1:1 假设算出 PPT 侧的对应位置，
         只有该位置上的记号确实相等才认这段——PPT 是手工谱，若它这行多写/漏写一个小节，
         端点就对不上，这段自动作废（自带版本一致性校验）。
    """
    blocks = anchor_blocks(pdf_grades, ppt_grades, min_len)
    anchor_len = sum(n for _i, _j, n in blocks)
    known = sum(1 for g in pdf_grades if g)
    coverage = anchor_len / max(1, min(known, len(ppt_grades)))
    if anchor_len < pair_min or coverage < cov_min:
        return []
    votes: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()

    def collect(new: list[tuple[int, str]]) -> None:
        """同一票只记一次：区间、两端点验证三条路径覆盖同一位置时会重叠"""
        for cp, grade in new:
            if (cp, grade) not in seen:
                seen.add((cp, grade))
                votes.append((cp, grade))

    for k in range(len(blocks) - 1):                      # ③ 锚点之间的区间
        i1, j1, n1 = blocks[k]
        i2, j2, _n2 = blocks[k + 1]
        collect(_gap_votes(pdf_elems, pdf_grades, ppt_grades, i1 + n1, i2, j1 + n1, j2))
    for step in (1, -1):                                  # ③④ 锚点 ↔ 下一个已知音级
        for i, j, n in blocks:
            a0 = i + n if step > 0 else i - 1
            b0 = j + n if step > 0 else j - 1
            if not (0 <= a0 < len(pdf_grades) and 0 <= b0 < len(ppt_grades)):
                continue
            a1 = a0
            while 0 <= a1 < len(pdf_grades) and pdf_grades[a1] is None:
                a1 += step
            if not (0 <= a1 < len(pdf_grades)):
                continue                                  # 该侧没有已知音级可当端点
            b1 = b0 + (a1 - a0)                           # 1:1 假设下的对应位置
            if not (0 <= b1 < len(ppt_grades)) or pdf_grades[a1] != ppt_grades[b1]:
                continue                                  # 端点验证失败 → 这段不可信
            if step > 0:
                collect(_gap_votes(pdf_elems, pdf_grades, ppt_grades, a0, a1, b0, b1))
            else:
                collect(_gap_votes(pdf_elems, pdf_grades, ppt_grades,
                                   a1 + 1, a0 + 1, b1 + 1, b0 + 1))
    return votes


def _learn_once(samples, applied, min_len, min_votes, min_ratio, pair_min, cov_min):
    """单轮学习：以 `applied` 为锚点来源，对**未解码**码位投票

    返回 `(learned, stats, info)`；`learned` 只含本轮够票数、无争议的码位。
    """
    votes: dict[int, dict[str, int]] = {}
    pairs = rows_seen = 0
    for pdf_rows, ppt_lines in samples:
        ppt_seqs = []
        for ln in ppt_lines:
            _syms, grades = ppt_grade_seq(ln.get("notes") or "")
            if len(grades) >= min_len:
                ppt_seqs.append(grades)
        if not ppt_seqs:
            continue
        for row in pdf_rows:
            elems = learn_elements(row, applied)
            grades = grade_seq([e.sym for e in elems])
            if sum(1 for g in grades if g) < min_len:
                continue
            rows_seen += 1
            for pgrades in ppt_seqs:
                pair_votes = votes_from_pair(elems, grades, pgrades, min_len, pair_min, cov_min)
                if pair_votes:
                    pairs += 1
                for cp, grade in pair_votes:
                    dist = votes.setdefault(cp, {})
                    dist[grade] = dist.get(grade, 0) + 1
    learned: dict[int, str] = {}
    stats: dict[int, tuple[str, int, int]] = {}
    for cp, dist in votes.items():
        sym, n = max(dist.items(), key=lambda kv: kv[1])
        total = sum(dist.values())
        stats[cp] = (sym, n, total)
        if n >= min_votes and n / total >= min_ratio:
            learned[cp] = sym
    info = {"pairs": pairs, "rows": rows_seen, "codepoints": len(votes)}
    return learned, stats, info


def geometry_marks(samples, applied=None):
    """几何可直接判定的「线类」码位 → `{码位: '|'}`

    小节线/终止线不占时值，PPT 侧本就不记（`ppt_symbols` 已剔除），所以跨源学习永远拿不到
    它的票；但它在 PDF 里是独立字符、会出现在 `code_seq` 里，不标就永远是 `?`。两条几何判据：
      ① 墨迹高 > `P.ROW_MAX_IH`：小节线 ih≈1.13、行首尾双纵线 1.15~1.16（音符 ≤0.50）；
      ② 墨迹宽 < `LINE_MARK_MAX_W`：极窄的纵线（细小节线 `601d` ≈0.04）——高不一定超高，
         但宽度远小于任何音符字形（最窄的音符 `1` ≈0.14）。
    """
    out: dict[int, str] = {}
    skip = applied or {}
    for pdf_rows, _lines in samples:
        for row in pdf_rows:
            for c in row.elements:
                if c.cp in skip:
                    continue
                if c.ih > P.ROW_MAX_IH or 0.0 < c.iw < LINE_MARK_MAX_W:
                    out[c.cp] = P.LINE_MARK
    return out


def learn_anchors(samples, mapping=None, rounds=3, min_len=ANCHOR_MIN_LEN,
                  min_votes=ANCHOR_MIN_VOTES, min_ratio=ANCHOR_MIN_RATIO,
                  pair_min=ANCHOR_PAIR_MIN, cov_min=PAIR_COV_MIN):
    """跨源「拍位级」学习：`samples=[(PDF 谱行列表, PPT 行列表), ...]`

    为什么多轮：映射是**自举**出来的——一轮学到的音级，下一轮就成了新锚点，能把更多
    未解码码位夹进「锚点 ↔ 已验证端点」的区间里。实测单轮只有十几个码位有票，多轮才能
    滚动覆盖（轮内只对未解码码位投票，所以不会重复学习、也不会跨轮冲突）。

    返回 `(learned, stats, info)`：
      learned `{码位: 音级}`；stats `{码位: (音级, 票数, 总票数)}`（`db.save_codepoint_map` 可直接用）；
      info 含 `pairs/rows/codepoints/rounds`。`mapping` 提供首轮锚点（缺省只用 `MANUAL_SEED`）。
    """
    applied = {**MANUAL_SEED, **(mapping or {})}
    learned: dict[int, str] = {}
    stats: dict[int, tuple[str, int, int]] = {}
    info: dict[str, int] = {"pairs": 0, "rows": 0, "codepoints": 0, "rounds": 0}
    for r in range(1, max(1, rounds) + 1):
        one, one_stats, one_info = _learn_once(samples, applied, min_len, min_votes,
                                               min_ratio, pair_min, cov_min)
        fresh = {cp: sym for cp, sym in one.items() if cp not in learned}
        info = {**one_info, "rounds": r}
        if not fresh:
            break
        learned.update(fresh)
        stats.update(one_stats)
        applied.update(fresh)
    marks = geometry_marks(samples, applied)
    for cp, sym in marks.items():
        learned.setdefault(cp, sym)
        stats.setdefault(cp, (sym, 1, 1))
    info["marks"] = len(marks)
    return learned, stats, info
