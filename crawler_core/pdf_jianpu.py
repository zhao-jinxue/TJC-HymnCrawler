# crawler_core/pdf_jianpu.py
# 从官方简谱 PDF（Hymn_Downloads/<目录>/N_简谱.pdf）抽取「音符坐标 + 歌词坐标」并做逐字对位（POC）
#
# 为什么做（2026-09-14 取证，见 docs/sessions/2026-09-13_19-18-00.md 任务 4）：
#   官方简谱 PDF 是 iTextSharp 4.1.6 生成的**矢量 PDF**：音符 = 嵌入字体 `MMP2005`
#   （Mumin Music 2005 简谱字体）的**文本**（28pt），歌词 = 中文字体文本（14pt），
#   谱线/小节线用 1×1 与 10×1 的**微型图片**绘制（一份 2132 个），没有扫描页。
#   → 每个音符、每个字都带精确坐标，可直接做几何对位，**无需 OCR**。
#
# 实测结构（474 份中 473 份同构；唯一例外 #349 无 MMP2005）：
#   - 一首 = 若干「乐句组」，每组 = 4 行谱（SATB 四声部）+ 3 行歌词（一谱多词共用）
#   - 每行谱的**主层**（size≈28pt 的 MMP2005 字符）沿 x 排列，每拍 ≈ 21pt；
#     元素 = 音符字形或延长线；它们之间夹着空白占位字形（不下墨、只推光标）
#   - 音符是**字体私有 CJK 码位**（实测 #1：`4E52`=1 / `4E56`=3 / `4E59`=5 / `4E5C`=6 …），
#     延长线另有码位，行首/行尾记号贴着页边距（x≈76 / x≥512）
#   - 小节线在 PDF 里**不是文本**（微型图片绘制）→ 与 PPT 对齐时必须从 `notes` 里剔除
#
# 对位原理：
#   1. 取 PDF 某谱行的元素序列（去空白占位、去贴边记号）→ 码位序列
#   2. 与 DB 里 PPT 的 `notes`（去小节线）比对：**长度相等且逐位同构**（同码位↔同记号）
#      → 判定为同一行；同时产出「码位 → 记号」映射（多首累计即得高置信度映射表）
#   3. 歌词行汉字的 x 与元素 x 做最近邻（Δ 阈值内）；落在两元素中点者判为「一字多音」
#
# 定位：POC（只做抽取 + 对位 + 出图，不写库）。全量落地时再扩 DB v9 两表与第 6 项跨源校验。
import io
import os
import re
from dataclasses import dataclass, field

from . import config

# PDF 字体子集前缀（6 个大写字母 + `+`，各文件随机，如 ABCDEE+ / BCDLEE+）
_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")

# 官方简谱 PDF 根目录（大字库资源，随 README 说明获取；不入 git）
PDF_ROOT = config.SAVE_ROOT
# 简谱字体（音符/延长线全由它渲染）
NOTE_FONT = "MMP2005"
# 空白占位码位：字形不下墨，只占一个 7pt 光标位（`3021` 是字体给空白起的 CJK 码位）
BLANK_CODEPOINTS = frozenset({0x20, 0x3021})
# 贴边阈值（pt）：小于它的是行首记号，大于它的是行尾记号（实测行首 x≈76 / 行尾 x≥512）
HEAD_X_MAX = 90.0
TAIL_X_MIN = 512.0
# 音符字号窗口（pt）：主层 28pt，装饰层（减时线/连音线）也是同字体但另行成层
NOTE_SIZE_MIN = 26.0
NOTE_SIZE_MAX = 29.0
# 歌词字号窗口（pt）：汉字 14pt（另有 12pt 的少量符号）
LYRIC_SIZE_MIN = 12.0
LYRIC_SIZE_MAX = 15.0
# 对位容差（pt）：汉字中心与元素中心的距离上限；超过则认为落在两元素之间
ALIGN_TOL = 8.0
# 歌词窗口（pt）：谱行下方多远以内的歌词行属于该谱行（再往下就是下一乐句/其它声部）
LYRIC_WINDOW = 80.0
# 小标记（不占时值）的墨迹判据（em 归一化）——实测 MMP2005 墨迹（宽×高）：
#   音符 0.14~0.27 × 0.34~0.50；延长线 0.21 × 0.046；减时线 0.26 × 0.020；附点 0.073 × 0.073；
#   小节线 0.22 × 0.39（与音符同尺度，需靠码位学习剔除）；行首/行尾记号高 > 1.1（另有贴边判据）
#   → 「极矮」= 减时线/短横；「又矮又窄」= 附点等小标记；两者都不占时值
MARK_MAX_H = 0.03
DOT_MAX_H = 0.10
DOT_MAX_W = 0.15
# PPT 记号里不参与对齐的字符：小节线/终止线/双纵线（PDF 侧不是文本）+ 括号
PPT_SKIP_CHARS = set("\\|?[]()")
# PPT 零宽叠加修饰（附点/八度点/升号/连音线/减时线碎片）——不下推光标，对齐时剔除
PPT_OVERLAY_CHARS = set("089=-ikKPpo_+:.*^~'\"%&")
# 映射表里的「线类」记号：PDF 侧有字符、PPT 侧被剔除（小节线/终止线/双纵线）
LINE_MARK = "|"
# 拍位间距下限（pt）：未知码位与前一元素的 x 间距小于它 → 紧贴的修饰（附点等）而非音符
#   实测：拍位间距 ≈21pt，音符与附点间距 ≈3.5pt（半格），取 15pt 作分界
BEAT_MIN_GAP = 15.0


@dataclass(frozen=True)
class PdfChar:
    """PDF 里的一个字符（含精确坐标；坐标为 pt，原点在左上角）"""

    cp: int          # 码位（Identity-H 下即字体私有码位）
    font: str        # 字体名（去掉 `ABCDEE+` 前缀）
    size: float      # 字号（pt）
    x0: float        # bbox 左（advance 框）
    y0: float        # bbox 上
    x1: float        # bbox 右
    y1: float        # bbox 下
    page: int = 0    # 页号（0 起）
    iw: float = 0.0  # 字形墨迹宽（em 归一化；0 = 未知）
    ih: float = 0.0  # 字形墨迹高（em 归一化；0 = 未知）

    @property
    def cx(self) -> float:
        """bbox 水平中心（对位用）"""
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        """bbox 垂直中心（分行用）"""
        return (self.y0 + self.y1) / 2

    @property
    def is_dot(self) -> bool:
        """是否「不占时值的小标记」：极矮的横线（减时线）或又矮又窄的点（附点等）"""
        if self.ih <= 0.0:
            return False
        if self.ih < MARK_MAX_H:
            return True
        return self.ih < DOT_MAX_H and self.iw < DOT_MAX_W


@dataclass
class SheetRow:
    """一行谱（同一 y 层的音符字符集合）"""

    y: float
    page: int
    chars: list[PdfChar] = field(default_factory=list)

    @property
    def elements(self) -> list[PdfChar]:
        """**时值元素**：去掉空白占位、贴边行首/行尾记号与小标记（附点等），按 x 升序"""
        out = [c for c in self.chars
               if c.cp not in BLANK_CODEPOINTS
               and not c.is_dot
               and HEAD_X_MAX <= c.cx <= TAIL_X_MIN]
        out.sort(key=lambda c: c.cx)
        return out

    @property
    def dots(self) -> list[PdfChar]:
        """小标记（附点/八度点等，不占时值；仅供出图与报告展示）"""
        return sorted((c for c in self.chars if c.is_dot), key=lambda c: c.cx)

    @property
    def edges(self) -> list[PdfChar]:
        """贴边记号（行首/行尾，通常双纵线/终止线；不计时值）"""
        return sorted((c for c in self.chars
                       if c.cp not in BLANK_CODEPOINTS
                       and (c.cx < HEAD_X_MAX or c.cx > TAIL_X_MIN)), key=lambda c: c.cx)


@dataclass(frozen=True)
class AlignCell:
    """一个音节与其对应音符元素的几何对位结果"""

    syllable: str      # 歌词汉字
    sx: float          # 字中心 x
    index: int         # 元素序号（0 起；-1 = 未对位）
    cp: int            # 元素码位（-1 = 未对位）
    ex: float          # 元素中心 x
    delta: float       # |sx - ex|
    span: int = 1      # 该字覆盖的元素个数（2 = 一字多音）


# ================= 二、PDF → 字符坐标 =================

def _import_pymupdf():
    """延迟导入 pymupdf（缺失时给出可执行的修复提示）

    注意：PyPI 上另有 `fitz` 0.0.1.dev2 的空壳包同名占坑（`import fitz` 会报
    `No module named 'frontend'`，且与 PyMuPDF 无对应关系），故本项目统一用 `import pymupdf`。
    """
    try:
        import pymupdf  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "解析官方简谱 PDF 需要 pymupdf：pip install -r config/requirements.txt") from exc
    return pymupdf


def _font_name(raw):
    """字体名去掉子集前缀（`ABCDEE+` / `BCDLEE+` 等 6 字母 + `+`）；中文名是 GBK 字节被当
    latin-1 显示，这里还原（失败则原样）"""
    name = _SUBSET_RE.sub("", raw or "")
    try:
        return name.encode("latin1").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def pdf_path(hymn_number, root=None):
    """新版编号 → 官方简谱 PDF 路径（目录名形如 `001_1頌讚獨一真神`）

    返回 None 表示该编号没有对应目录/文件（如反查不到新版编号的旧版诗）。
    """
    root = root or PDF_ROOT
    if not os.path.isdir(root):
        return None
    prefix = f"{int(hymn_number):03d}_"
    for name in sorted(os.listdir(root)):
        if not name.startswith(prefix):
            continue
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith("_简谱.pdf"):
                return os.path.join(d, fn)
    return None


def _font_ink_boxes(doc):
    """从 PDF 嵌入的简谱字体提取 {码位: (墨迹宽, 墨迹高)}（em 归一化）

    为什么需要：PyMuPDF 给的字符 bbox 是 **advance 框**（实测恒为 7.0×28.0pt），
    无法区分「音符 / 延长线 / 附点」；而附点是独立字符、不占时值，必须剔掉才能与
    PPT 的记号序列对齐。字形墨迹框（glyf 的 xMin/xMax/yMin/yMax）能干净区分：
    音符高 ≈0.5em，附点又矮又窄，延长线是矮而宽的横线。
    """
    out: dict[int, tuple[float, float]] = {}
    try:
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - 无 fontTools 时退化为「不过滤小标记」
        return out
    for xref in range(1, doc.xref_length()):
        try:
            name, _ext, _subtype, buf = doc.extract_font(xref)
        except Exception:  # noqa: BLE001,S112 - 单个字体对象损坏不应中断整份解析
            continue
        if _font_name(name) != NOTE_FONT or not buf:
            continue
        try:
            tt = TTFont(io.BytesIO(buf), lazy=True)
            upm = getattr(tt["head"], "unitsPerEm", 1000) or 1000
            glyf = tt["glyf"]
            for cp, gname in (tt.getBestCmap() or {}).items():
                g = glyf[gname]
                xmin, xmax = getattr(g, "xMin", None), getattr(g, "xMax", None)
                ymin, ymax = getattr(g, "yMin", None), getattr(g, "yMax", None)
                if xmin is None or xmax is None or ymin is None or ymax is None:
                    continue                          # 空字形（如空格）
                out[cp] = ((xmax - xmin) / upm, (ymax - ymin) / upm)
        except Exception:  # noqa: BLE001,S112 - 字体解析失败只影响小标记过滤
            continue
    return out


def read_chars(path):
    """PDF → [PdfChar]（全部页；含简谱字体与歌词字体，坐标为 pt，原点左上）"""
    pymupdf = _import_pymupdf()
    out: list[PdfChar] = []
    doc = pymupdf.open(path)
    try:
        boxes = _font_ink_boxes(doc)
        for pno in range(doc.page_count):
            page = doc[pno]
            for span in page.get_texttrace():
                font = _font_name(span["font"])
                size = float(span["size"])
                for ch in span["chars"]:
                    cp, bbox = ch[0], ch[3]
                    iw, ih = boxes.get(cp, (0.0, 0.0))
                    out.append(PdfChar(cp=cp, font=font, size=size, x0=bbox[0], y0=bbox[1],
                                       x1=bbox[2], y1=bbox[3], page=pno, iw=iw, ih=ih))
    finally:
        doc.close()
    return out


def sheet_rows(chars, size_min=NOTE_SIZE_MIN, size_max=NOTE_SIZE_MAX):
    """字符 → 谱行列表（每个 y 层一行，按页内 y 升序；跨页按页序拼接）

    同一行谱的装饰（减时线/连音线）落在别的 y 层，会各自成行——调用方用
    `melody_rows()` 只取「元素数够多」的主层，装饰层自然被过滤掉。
    """
    layers: dict[tuple[int, float], list[PdfChar]] = {}
    for c in chars:
        if c.font != NOTE_FONT or not (size_min <= c.size <= size_max):
            continue
        layers.setdefault((c.page, round(c.cy, 1)), []).append(c)
    rows = [SheetRow(y=y, page=p, chars=v) for (p, y), v in layers.items()]
    rows.sort(key=lambda r: (r.page, r.y))
    return rows


def melody_rows(chars, min_elems=4):
    """旋律行候选：元素（音符/延长线）数 ≥ min_elems 的谱行，按页内 y 升序"""
    return [r for r in sheet_rows(chars) if len(r.elements) >= min_elems]


def lyric_rows(chars, size_min=LYRIC_SIZE_MIN, size_max=LYRIC_SIZE_MAX, cjk_only=True):
    """歌词行：按 (页, y) 聚类的**非简谱字体**字符（汉字为主；一谱多词则是多行）

    cjk_only=True 只保留 CJK 汉字（剔除空格占位与半角标点）→ 得到「实字序列」。
    """
    layers: dict[tuple[int, float], list[PdfChar]] = {}
    for c in chars:
        if c.font == NOTE_FONT or not (size_min <= c.size <= size_max):
            continue
        if cjk_only and not (0x3400 <= c.cp <= 0x9FFF):
            continue
        layers.setdefault((c.page, round(c.cy, 1)), []).append(c)
    rows = [sorted(v, key=lambda c: c.cx) for _, v in sorted(layers.items())]
    return [r for r in rows if len(r) >= 2]



# ================= 三、与 PPT notes 结构同构匹配 + 码位学习 =================

def ppt_symbols(notes):
    """PPT notes → 参与对位的记号序列（剔除空白、零宽修饰、小节线类）

    小节线类必须剔除：PDF 侧的小节线是**微型图片**而非文本，元素序列里没有它们。
    """
    return [ch for ch in notes
            if not ch.isspace()
            and ch not in PPT_OVERLAY_CHARS
            and ch not in PPT_SKIP_CHARS]


def row_matches(codepoints, symbols):
    """码位序列与 PPT 记号序列**逐位同构**？是则返回 {码位: 记号}，否则 None

    同构 = 长度相等 且 同一码位始终对应同一记号（同音级必同字形）。
    该判据同时完成两件事：**声部/行定位**（只有与 PPT 记谱一致的那行谱才会命中）
    与 **码位语义学习**（命中的映射即语料）。
    """
    if not codepoints or len(codepoints) != len(symbols):
        return None
    table: dict[int, str] = {}
    for cp, sym in zip(codepoints, symbols):
        if table.setdefault(cp, sym) != sym:
            return None
    return table


def decode_elements(elements, mapping):
    """用已知映射解码元素序列 → [记号]；**未知码位视为修饰**（附点/减时线/八度点）合并掉

    为什么需要：PDF 把附点、减时线、八度点渲染成**独立字符**，而 PPT 用**合成字形**
    （如 `t` = 5 + 减时线），因此 PDF 元素数常大于 PPT 记号数；把已学映射当锚点、
    未知码位当修饰，即可用「已学知识」解码其余行（自举）。
    """
    out = []
    for e in elements:
        sym = mapping.get(e.cp)
        if sym is None:
            if out:
                continue          # 修饰：合并到前一个已解码元素
            return None           # 序列开头就是未知码位 → 无法解码
        out.append(sym)
    return out


def modifiers_of(elements, mapping):
    """序列里被判为「修饰」（不在映射表内）的码位集合（自举第二轮学习用）"""
    return {e.cp for e in elements if e.cp not in mapping}


def align_sequence(elements, symbols, known):
    """把 PDF 元素序列对齐到 PPT 记号序列（允许跳过「线类」与「紧贴的修饰」）

    返回 (映射表, 被跳过的码位列表)；无法对齐返回 None。
    - `known[cp] == LINE_MARK`：已知线类（小节线/终止线，PPT 侧已剔除）→ 跳过
    - 未知码位按**几何**决策：与前一元素间距 < `BEAT_MIN_GAP` → 紧贴的修饰（附点等）跳过；
      间距足一个拍位 → 当作音符，当场学出 `码位 → 记号`（破解「新音级没有映射就永远匹配不上」
      的死锁）；同一行内若同一码位要对应两个不同记号，则判定对齐失败（防止乱凑）。
    已映射码位必须**逐位对上** PPT 记号，约束很强，可有效抑制假匹配。
    """
    table = dict(known)
    skipped: list[int] = []
    i, j = 0, 0
    n, m = len(elements), len(symbols)
    while j < m:
        new_cp = None
        while i < n:
            cp = elements[i].cp
            sym = table.get(cp)
            if sym == LINE_MARK:
                i += 1
                continue
            if sym is None:
                gap = elements[i].cx - elements[i - 1].cx if i > 0 else BEAT_MIN_GAP
                if gap < BEAT_MIN_GAP:
                    skipped.append(cp)
                    i += 1
                    continue
                new_cp = cp                      # 落在拍位上 → 当音符（边对齐边学）
            break
        if i >= n:
            return None
        if new_cp is not None:
            table[new_cp] = symbols[j]
        elif table[elements[i].cp] != symbols[j]:
            return None
        i += 1
        j += 1
    while i < n:                                 # 尾部剩余元素也必须可跳过
        sym = table.get(elements[i].cp)
        if sym is not None and sym != LINE_MARK:
            return None
        if sym is None:
            skipped.append(elements[i].cp)
        i += 1
    return table, skipped


def match_rows_to_lines(rows, lines, min_elems=4, mapping=None):
    """PDF 旋律行 × DB 行（notes）结构同构匹配（两级）

    - `mapping=None`：**严格**匹配（1:1，用于第一轮挖出基础「码位 → 记号」映射）
    - `mapping={...}`：**自举**匹配（已知映射当锚点，线类与未知码位可跳过）
    返回 [(row_index, row, line, table, skipped), ...]；同一 PDF 行可命中多行 DB 记录
    （一谱多词：3 节歌词共用同一行谱）——这本身也是「一谱多词」的交叉校验。
    """
    out = []
    for i, row in enumerate(rows):
        elems = row.elements
        if len(elems) < min_elems:
            continue
        cps = [e.cp for e in elems]
        for line in lines:
            symbols = ppt_symbols(line.get("notes") or "")
            if mapping is None:
                table = row_matches(cps, symbols)
                if table:
                    out.append((i, row, line, table, []))
            else:
                got = align_sequence(elems, symbols, mapping)
                if got:
                    out.append((i, row, line, got[0], got[1]))
    return out


def bootstrap(rows, lines, min_elems=4, rounds=4):
    """单首自举：先严格匹配挖基础映射，再用映射宽松对齐（线类/未知可跳过），迭代若干轮

    返回 (matches, learned)；matches 为最后一轮命中（按谱行 y 序）。
    """
    learned, _stats, _hits = learn_across([(rows, lines)], min_elems=min_elems, rounds=rounds)
    matches = match_rows_to_lines(rows, lines, min_elems=min_elems, mapping=learned or None)
    return matches, learned


def learn_codepoint_map(pairs):
    """[(码位, 记号), ...] → {码位: (记号, 票数, 总票数)}（跨首累计，冲突分开计票）"""
    votes: dict[int, dict[str, int]] = {}
    for cp, sym in pairs:
        votes.setdefault(cp, {})
        votes[cp][sym] = votes[cp].get(sym, 0) + 1
    return {cp: (max(d.items(), key=lambda kv: kv[1])[0], max(d.values()), sum(d.values()))
            for cp, d in votes.items()}


# ================= 四、逐字对位 =================

def align_lyric(syllables, elements, tol=ALIGN_TOL):
    """歌词汉字（PdfChar）↔ 元素序列 → [AlignCell]（最近邻；一字多音另标 span=2）

    诚实边界：作者是按字形宽度手工排版的，**没有严格的一字一音数据保证**——
    因此这里输出的是「几何候选 + Δ 偏差」，低于 `tol` 视为可靠，落在两元素中点的
    标 `span=2`（一字多音），供 `tool/show_pdf_align.py` 出图人工终审。
    """
    out: list[AlignCell] = []
    for s in syllables:
        if not elements:
            out.append(AlignCell(syllable=chr(s.cp), sx=s.cx, index=-1, cp=-1, ex=0.0, delta=-1.0))
            continue
        idx, delta = min(((i, abs(s.cx - e.cx)) for i, e in enumerate(elements)),
                         key=lambda t: t[1])
        span = 1
        if delta > tol:                  # 更接近「与相邻元素的中点」= 一字跨两音
            for j in (idx - 1, idx + 1):
                if 0 <= j < len(elements):
                    mid = (elements[idx].cx + elements[j].cx) / 2
                    if abs(s.cx - mid) < delta:
                        span = 2
                        break
        out.append(AlignCell(syllable=chr(s.cp), sx=s.cx, index=idx,
                             cp=elements[idx].cp, ex=elements[idx].cx, delta=delta, span=span))
    return out


def lyric_blocks(rows, gap=20.0):
    """歌词行 → 按 y 间隔聚成「乐句块」（每块 = 一谱多词的那几行歌词，如 3 行）"""
    blocks: list[list[list[PdfChar]]] = []
    cur: list[list[PdfChar]] = []
    last_y = None
    for r in rows:
        y = r[0].cy
        if last_y is not None and y - last_y > gap:
            blocks.append(cur)
            cur = []
        cur.append(r)
        last_y = y
    if cur:
        blocks.append(cur)
    return blocks


def learn_across(samples, min_elems=4, rounds=4):
    """跨多首**联合自举**学习「码位 → 记号」映射（含线类判定）

    samples = [(rows, lines), ...]（每首的旋律行与 DB 行）
    为什么必须跨首：单首里可能一行「干净谱」都没有（行行都带附点/减时线/小节线字符），
    严格匹配无从启动；跨首联合后，干净行先交出核心映射，再滚雪球解码其余行。
    线类（PDF 有、PPT 侧被剔除的小节线/终止线）由「被跳过次数 ≥2 且从未成为音符」投票固化。
    返回 (learned, stats, hits)：learned={码位: 记号|LINE_MARK}，stats 含票数，hits=末轮命中处数。
    """
    learned: dict[int, str] = {}
    pending: dict[int, int] = {}          # 候选线类：码位 → 被跳过次数
    conflicts: dict[int, set[str]] = {}   # 冲突记录：码位 → 出现过的不同记号
    votes: list[tuple[int, str]] = []
    hits = 0
    for r in range(rounds):
        hits = 0
        round_votes: list[tuple[int, str]] = []
        for rows, lines in samples:
            # 第 0 轮走严格匹配（挖基础映射）；之后一律走「带跳过」的宽松对齐，
            # 即使映射仍为空（整首都没有干净谱时靠它启动，否则会死锁在「无映射→无匹配」）
            matches = match_rows_to_lines(rows, lines, min_elems=min_elems,
                                          mapping=None if r == 0 else learned)
            hits += len(matches)
            for _i, _row, _line, table, skipped in matches:
                for cp, sym in table.items():
                    if sym and sym != LINE_MARK:
                        round_votes.append((cp, sym))
                for cp in skipped:
                    pending[cp] = pending.get(cp, 0) + 1
        before = len(learned)
        for cp, sym in round_votes:       # 先到先得；同一码位出现两种记号 → 判为冲突并剔除
            old = learned.get(cp)
            if old is None:
                learned[cp] = sym
            elif old != sym:
                conflicts.setdefault(cp, set()).update((old, sym))
        for cp in conflicts:
            learned.pop(cp, None)
        for cp, cnt in pending.items():   # 反复被跳过且从未成为音符 → 固化为线类
            if cnt >= 2 and cp not in learned and cp not in conflicts:
                learned[cp] = LINE_MARK
        votes = round_votes
        if r == rounds - 1 or (r and len(learned) == before):
            break
    return learned, learn_codepoint_map(votes), hits


def analyze(pdf_chars, lines, min_elems=4, tol=ALIGN_TOL, learned=None):
    """一次完整分析：匹配 + 学习 + 逐字对位

    lines：DB `hymn_jianpu_line` 的行（需含 notes / lyric / stanza_no / line_no）
    learned：外部（跨首联合）学到的映射；None 时退化为单首自举
    返回 {"rows", "matches", "hit_rows", "pairs", "learned", "modifiers"}
    """
    rows = melody_rows(pdf_chars, min_elems=min_elems)
    if learned:
        matches = match_rows_to_lines(rows, lines, min_elems=min_elems, mapping=learned)
        used = dict(learned)
    else:
        matches, used = bootstrap(rows, lines, min_elems=min_elems)
    hit_rows: list[tuple[int, SheetRow]] = []
    for i, row, _line, _mapping, _sk in matches:
        if not hit_rows or hit_rows[-1][0] != i:
            hit_rows.append((i, row))
    lyrics = lyric_rows(pdf_chars)
    pairs = []
    for i, row in hit_rows:
        # 该谱行「下方窗口」内的歌词行（一谱多词 → 多行；上方的是标题/调号，剔除）
        cand = [r for r in lyrics
                if r[0].page == row.page and row.y < r[0].cy <= row.y + LYRIC_WINDOW]
        if not cand:
            continue
        block = lyric_blocks(cand)[0]
        line = next((ln for j, _r, ln, _m, _sk in matches if j == i), None)
        cells = align_lyric(block[0], row.elements, tol=tol)
        pairs.append({"row": row, "block": block, "cells": cells, "line": line})
    mods = set()
    for _i, row, _line, _m, _sk in matches:
        mods |= modifiers_of(row.elements, used)
    votes = [(cp, sym) for _i, _r, _l, table, _sk in matches
             for cp, sym in table.items() if sym and sym != LINE_MARK]
    return {"rows": rows, "matches": matches, "hit_rows": hit_rows,
            "blocks": lyric_blocks(lyrics), "pairs": pairs,
            "learned": learn_codepoint_map(votes), "modifiers": mods,
            "line_marks": {cp for cp, sym in used.items() if sym == LINE_MARK}}

