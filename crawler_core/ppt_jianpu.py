# crawler_core/ppt_jianpu.py
# 从《赞美诗》PPT（data/赞美诗PPT/*.ppt）提取「带简谱的文字歌词」
#
# 取证结论（2026-09-14，见 docs/sessions/2026-09-13_19-18-00.md 任务 3）：
#   1. `*.ppt` 是二进制 PPT（OLE/CFB 复合文档），文本存在 "PowerPoint Document" 流的
#      TextCharsAtom(0x0FA0, UTF-16LE) / TextBytesAtom(0x0FA8) 记录里，
#      前一个 TextHeaderAtom(0x0F9F) 给出该文本的角色（0=母版标题, 1=母版正文, 4=页标题, 5=正文）。
#   2. **每张幻灯片 = 一节（stanza）**；正文文本框（角色 5）以 `\r` 分行：
#        第 1 行 = 「编号(旧版)、标题  调号 拍号 ♩=速度」（仅第 1 张有完整标题行）
#        其后严格交替「简谱记号行」「歌词行」
#        末行 = `k/M` 节号（k 序号 / M 总节数）
#   3. 记号是**纯 ASCII**（无私有区码位），字体只负责渲染：
#        1-7 = 音级（简谱数字）；0 = 休止/叠加修饰
#        q w e t y r u / Q W E R T Y U 等字母 = 「数字 + 减时线/八度点」合成字形
#        `/` = 延长线（时值延长一拍，见 #5 `1//` ≙ 官方 `1 - -`）
#        `\` = 小节线；`|` = 终止线；`?` = 双纵线
#        零宽码位（0 8 9 = - i k P _ + 等）= 附点/八度点/升降号等叠加修饰（不下推光标）
#       —— 字形语义由 `data/赞美诗PPT/简谱字体/简谱字体.ttf`（01SMN）落图后逐一比对官方简谱确认。
#   4. `简谱字体.ttf`（01SMN "Simple music notation"）渲染记号，`歌词字体.ttf`（DFKai-SB）
#      渲染歌词；未安装字体时 PPT 里的记号行看起来是"乱码/空白"，装字体才显示成简谱。
#
# 职责：
#   read_ppt_text()  二进制 PPT → [(角色, 文本)]
#   parse_hymn_ppt() 单首 PPT → 结构化记录（元数据 / 每节每行 notes+lyric / 校验标志）
#   extract_all()    目录级批量提取 + 统计
#   count_notes() / count_syllables()  音符数与音节数（等长校验用）

import difflib
import hashlib
import os
import re

from .config import DATA_DIR, DB_PATH

# PPT 目录（大字库资源，随 README 说明获取；不入 git）
PPT_DIR = os.path.join(DATA_DIR, "赞美诗PPT")
# 校验报告产物
REPORT_PATH = os.path.join(DATA_DIR, "jianpu_report.txt")
# 提取器版本（写库溯源；解析规则变更时递增）
EXTRACTOR = "ppt_jianpu/1.0"

# ---- PPT 记录类型（[MS-PPT] 二进制格式）----
REC_TEXT_HEADER = 0x0F9F   # TextHeaderAtom：4 字节文本角色
REC_TEXT_CHARS = 0x0FA0    # TextCharsAtom：UTF-16LE 文本
REC_TEXT_BYTES = 0x0FA8    # TextBytesAtom：Latin-1 文本
RES_BODY = 5               # 文本角色 5 = 幻灯片正文

# ---- 记号字符语义（474 个 PPT 全量 60 种字符，逐个字形落图比对官方简谱确认）----
# 音符头：字形内含简谱数字（纯数字 / 数字+减时线 / 数字+八度点 的合成字形）
NOTE_HEADS = (set("1234567")      # 纯数字：原位音符
              | set("qwertyu")    # 数字 + 一条减时线（全宽）
              | set("QWERTYU")    # 数字 + 减时线 + 高八度点
              | set("!#$@")       # 高八度点变体（1̇ / 3̇ / 4̇ / 2̇）
              | set("adfghjs")    # 数字 + 三条减时线（半宽字形）
              | set("ADFGHJS"))   # 三条减时线 + 高八度点
# 休止符：占一拍但不落字（` = 0̲ 八分休止符）→ 等长比对时不计入音符数
REST_CHARS = set("`")
# 其余占列的记号：延长线 `/`、小节线 `\`、终止线 `|`、双纵线 `?`、括号等
NON_NOTE_COLUMNS = set("/\\|?[]()")
# 零宽叠加修饰（附点 / 八度点 / 升号 / 连音线 / 减时线碎片，不推光标）
OVERLAY_MARKS = set("089=-ikKPpo_+:.*^~'\"%&")
# 字体未收录或明显误输入的噪声字符（不参与计数，仅在报告中提示）
NOISE_CHARS = set("–—︱。、，,;；:：")
# 小节线类（跨节曲调比较时保留，仅做空白归一）
BARLINE_CHARS = set("\\|?")

CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")
MARKER_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
# 首行：`349 (475) 、救主正在等待   降E大调 3/4 ♩=88`
# 版本标记有两种写法：行首 `(甲)、万古灵磐` 与数字之后 `51 (乙)万古灵磐`
HEADER_RE = re.compile(
    r"^\s*(?:[\[（(『]\s*(?P<ver>[甲乙丙丁A-Za-z0-9]{1,3})\s*[)）』\]]\s*)?"
    r"(?P<no>\d+)\s*(?:[\[（(『]\s*(?P<ver2>[甲乙丙丁]{1,2})\s*[)）』\]]\s*)?"
    r"(?:\(\s*(?P<no_new>\d+)\s*\))?\s*[、.．]?\s*(?P<title>.*)$")
KEY_RE = re.compile(r"(?:[降升]?[A-Ga-g][大小]?\s*调)")
TIME_RE = re.compile(r"\b\d+\s*/\s*\d+\b")
TEMPO_RE = re.compile(r"[♩♪]\s*=?\s*\d+|[=＝]\s*\d+")
# 歌词中的标点（不计入音节）
PUNCT_RE = re.compile(r"[\s，。、；：！？「」『』（）()《》〈〉“”‘’…—～~·\-–—\.!?,;:\"'\[\]{}|/\\]+")
# 节标签行：`(副歌)` / `(三)` / `(3)`；也可能粘在标题行尾（如 `… ♩=80   (三)`）
LABEL_RE = re.compile(r"^\s*[（(]\s*(副歌|[一二三四五六七八九十]+|\d+)\s*[)）]\s*$")
LABEL_TAIL_RE = re.compile(r"[（(]\s*(副歌|[一二三四五六七八九十]+|\d+)\s*[)）]\s*$")
# 尾部噪声行（孤立的标点，如 `。`）
TRAILER_RE = re.compile(r"^[。.，,、；;：:！!？?…—\-–\s]+$")
# 中文数字 → 序数
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# 异体字/用字变体折叠（仅用于**比对**：opencc s2t 输出「爲」而库内多为「為」等）
VARIANT_FOLD = str.maketrans({"爲": "為", "裏": "裡", "祢": "你", "禰": "你",
                              "麽": "麼", "么": "麼", "什": "甚", "哪": "那", "牠": "他"})
# 版本标记（DB 用 51_a/51_b + 标题 (甲)/(乙) 后缀；PPT 用 (甲)/(乙) 或文件名 a/b）
CN_VER = set("甲乙丙丁")
VER_FROM_SUFFIX = {"a": "甲", "b": "乙", "c": "丙", "d": "丁"}




# ================= 一、二进制 PPT → 文本 =================

def _import_olefile():
    """延迟导入 olefile（缺失时给出可执行的修复提示）"""
    try:
        import olefile  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "解析二进制 .ppt 需要 olefile：pip install -r config/requirements.txt") from exc
    return olefile


def iter_records(data):
    """深度优先遍历 PPT 记录流 → 依次产出 (recType, payload)

    记录头 8 字节：recVer|recInstance(2) + recType(2) + recLen(4)；
    recVer == 0xF 表示容器（payload 为其子记录序列），否则为叶子记录。
    用显式栈而非递归，避免畸形文件把栈打爆。
    """
    stack = [(0, len(data))]
    while stack:
        offset, end = stack.pop()
        while offset + 8 <= end:
            ver_inst = int.from_bytes(data[offset:offset + 2], "little")
            rec_type = int.from_bytes(data[offset + 2:offset + 4], "little")
            rec_len = int.from_bytes(data[offset + 4:offset + 8], "little")
            body = offset + 8
            if body + rec_len > end:      # 记录越界 → 流已损坏，放弃本层
                break
            if ver_inst & 0x0F == 0x0F:   # 容器：先处理子记录，再回到本层剩余部分
                stack.append((body + rec_len, end))
                offset, end = body, body + rec_len
                continue
            yield rec_type, data[body:body + rec_len]
            offset = body + rec_len


def read_ppt_text(path):
    """二进制 PPT → [(文本角色, 文本)]（按文档顺序；角色由前一个 TextHeaderAtom 决定）"""
    olefile = _import_olefile()
    if not olefile.isOleFile(path):
        raise ValueError(f"不是 OLE/CFB 复合文档（非二进制 PPT）：{path}")
    ole = olefile.OleFileIO(path)
    try:
        if not ole.exists("PowerPoint Document"):
            raise ValueError(f"缺少 'PowerPoint Document' 流：{path}")
        data = ole.openstream("PowerPoint Document").read()
    finally:
        ole.close()

    role, out = None, []
    for rec_type, payload in iter_records(data):
        if rec_type == REC_TEXT_HEADER:
            role = int.from_bytes(payload[:4], "little")
        elif rec_type == REC_TEXT_CHARS:
            out.append((role, payload.decode("utf-16-le", "replace")))
        elif rec_type == REC_TEXT_BYTES:
            out.append((role, payload.decode("latin-1", "replace")))
    return out


# ================= 二、行分类 / 计数 / 归一 =================

def is_notation_line(line):
    """是否简谱记号行：无汉字，且非空白字符中记号字符占多数（≥60%）"""
    if not line.strip() or CJK_RE.search(line):
        return False
    chars = [c for c in line if not c.isspace()]
    if not chars:
        return False
    known = sum(1 for c in chars
                if c in NOTE_HEADS or c in REST_CHARS or c in NON_NOTE_COLUMNS
                or c in OVERLAY_MARKS or c in BARLINE_CHARS or c in ".-&")
    return known / len(chars) >= 0.6


def count_notes(notes):
    """音符数：只数「含音符头」的字形（休止符 / 延长线 / 小节线 / 叠加修饰都不算）"""
    return sum(1 for c in notes if c in NOTE_HEADS)


def count_rests(notes):
    """休止符数（` = 0̲）：占拍但不落字，单列统计便于核对拍数"""
    return sum(1 for c in notes if c in REST_CHARS)


def noise_chars(notes):
    """记号行里的噪声字符（字体未收录的 `–`/`︱` 或误输入的 `。` 等）→ 去重列表"""
    return sorted({c for c in notes or "" if c in NOISE_CHARS})


def count_syllables(lyric):
    """歌词音节数：汉字 1 字 1 音节；连续英文字母算 1 音节；标点/空白/数字不计"""
    text = PUNCT_RE.sub("", lyric or "")
    cjk = sum(1 for c in text if CJK_RE.match(c))
    words = len(re.findall(r"[A-Za-z]+", text))
    return cjk + words


def normalize_notes(notes):
    """记号归一（去全部空白）：用于跨节比对"曲调是否同一段旋律" """
    return "".join((notes or "").split())


def notes_signature(notes):
    """曲调签名：归一空白 + 去掉首尾小节线/终止线（末小节 `\\` 有无属排版习惯，非曲调差异）"""
    return normalize_notes(notes).strip("\\|?")


def normalize_text(text):
    """文本归一：繁简归一（opencc s2t）+ 异体字折叠 + 去标点空白 + 小写（比对用）"""
    conv = _s2t(text if isinstance(text, str) else (text or "")).translate(VARIANT_FOLD)
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", conv).lower()


_CC = None


def _s2t(text):
    """简体 → 繁体（opencc 不可用时原样返回，只影响匹配率、不影响解析）"""
    global _CC
    if _CC is None:
        try:
            from opencc import OpenCC  # type: ignore[import-untyped]
            _CC = OpenCC("s2t")
        except ImportError:  # pragma: no cover - 依赖缺失路径
            _CC = False
    return text if not _CC else _CC.convert(text)


# ================= 三、单首 PPT 解析 =================

def parse_header(line):
    """解析首行 `349 (475) 、救主正在等待   降E大调 3/4 ♩=88` → 元数据

    返回 old_no(PPT/旧版编号) / new_no(PPT 内括号标注) / ver(甲/乙版本标记) /
    tail_label(粘在行尾的节标签，如 `… ♩=80   (三)`) / title / key_sig / time_sig / tempo。
    """
    blank = {"old_no": "", "new_no": "", "ver": "", "tail_label": "",
             "title_line": (line or "").strip(),
             "title": "", "key_sig": "", "time_sig": "", "tempo": ""}
    m = HEADER_RE.match(line or "")
    if not m:
        return blank
    rest = m.group("title") or ""

    def cut(pattern):
        nonlocal rest
        hit = pattern.search(rest)
        if not hit:
            return ""
        rest = rest[:hit.start()] + " " + rest[hit.end():]
        return re.sub(r"\s+", "", hit.group(0))

    key_sig, time_sig, tempo = cut(KEY_RE), cut(TIME_RE), cut(TEMPO_RE)
    tail_label = ""
    hit = LABEL_TAIL_RE.search(rest)          # 粘在标题行尾的 `(三)` / `(副歌)`
    if hit:
        tail_label = hit.group(1)
        rest = rest[:hit.start()]
    title = re.sub(r"\s+", "", rest)
    title = title.strip("、．.,，:：;；-–—=♩♪·()（）")   # 去掉切剩的零星符号
    return {
        "old_no": m.group("no") or "", "new_no": m.group("no_new") or "",
        "ver": (m.group("ver") or m.group("ver2") or "").strip(), "tail_label": tail_label,
        "title_line": (line or "").strip(),
        "title": title, "key_sig": key_sig, "time_sig": time_sig, "tempo": tempo,
    }


def label_meta(raw):
    """节标签 → (label_kind, label_no)：`副歌`→chorus / `三`→verse 3（无法识别→空）"""
    raw = (raw or "").strip()
    if not raw:
        return "", 0
    if "副歌" in raw:
        return "chorus", 0
    return "verse", CN_NUM.get(raw, 0) or (int(raw) if raw.isdigit() else 0)


def parse_slide(text, stanza_no):
    """单张幻灯片正文 → 一节结构

    行序：可选标题行 → 可选节标签行（`(副歌)` / `(三)`）→「记号行 + 歌词行」若干对 → 节号 `k/M`。
    容错：节号后若还有零星噪声行（如孤立的「。」）一律丢弃；记号行若后面没有紧跟歌词行，
    则 lyric 置空并记入 orphans（结构异常，供报告复核）。
    """
    lines = [ln for ln in (l.rstrip() for l in (text or "").split("\r")) if ln.strip()]

    marker_k = marker_m = None
    for idx in range(len(lines) - 1, -1, -1):
        mk = MARKER_RE.match(lines[idx])
        if mk:
            marker_k, marker_m = int(mk.group(1)), int(mk.group(2))
            lines = lines[:idx]                      # 丢弃节号之后的噪声
            break
    while lines and TRAILER_RE.fullmatch(lines[-1]):  # 尾部纯标点行
        lines.pop()

    header = None
    tail_label = ""
    if lines and not is_notation_line(lines[0]):
        header = parse_header(lines.pop(0))
        tail_label = header.get("tail_label", "")

    label = tail_label
    label_kind, label_no = label_meta(label)

    pairs, orphans = [], []
    i = 0
    while i < len(lines):
        lm = LABEL_RE.match(lines[i])
        if lm:                                   # 节标签可能夹在行对之间（如副歌段起始）
            if not label:
                label = lm.group(1).strip()
                label_kind, label_no = label_meta(label)
            i += 1
            continue
        if is_notation_line(lines[i]):
            has_lyric = i + 1 < len(lines) and not is_notation_line(lines[i + 1])
            pairs.append({
                "line_no": len(pairs) + 1,
                "notes": lines[i].strip(),
                "lyric": lines[i + 1].strip() if has_lyric else "",
            })
            i += 2 if has_lyric else 1
        else:
            orphans.append(lines[i].strip())
            i += 1
    return {"stanza_no": stanza_no, "header": header, "marker_k": marker_k,
            "marker_m": marker_m, "label": label, "label_kind": label_kind,
            "label_no": label_no, "pairs": pairs, "orphans": orphans}


def tune_period(slides):
    """推断曲调周期 P 与一致性

    取每张幻灯片的记号签名（各记号行归一后拼接），若 `sig[i] == sig[i+P]` 对全部 i 成立，
    说明每 P 张幻灯片共用同一段旋律（P=1：逐节同调；P=2：一节正歌分两个半段，如 #5）。
    返回 (period, consistent)：无周期关系时返回 (0, False)。
    """
    sigs = ["|".join(notes_signature(p["notes"]) for p in s["pairs"]) for s in slides]
    if len(sigs) <= 1:
        return (1, True) if sigs else (0, False)
    for p in range(1, len(sigs)):                 # p == len(sigs) 会让 all() 空真，必须排除
        if all(sigs[i] == sigs[i + p] for i in range(len(sigs) - p)):
            return p, True
    return 0, False


# ================= 四、与 DB 比对（编号 / 正歌 / 歌词归属） =================

def load_db_index(db_path=DB_PATH):
    """读 DB → {"titles": {归一标题: [hymn_number...]}, "verses": {编号: [归一正歌...]},
              "choruses": {编号: 归一副歌}}

    标题与歌词都做繁简归一（PPT 是简体、官网/DB 是繁体），供编号与归属比对。
    """
    import sqlite3
    cols = ", ".join(f"verse_{i}" for i in range(1, 11))
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT hymn_number, title, chorus, {cols} FROM tjc_hymn").fetchall()
    finally:
        conn.close()
    titles, verses, choruses = {}, {}, {}
    for row in rows:
        no = str(row[0])
        titles.setdefault(normalize_text(row[1] or ""), []).append(no)
        choruses[no] = normalize_text(row[2] or "")
        verses[no] = [normalize_text(v or "") for v in row[3:13]]
    return {"titles": titles, "verses": verses, "choruses": choruses}


def match_hymn_number(title, index, ver=""):
    """PPT 标题 → DB hymn_number：归一精确 → 版本后缀（甲/乙）消歧 → difflib 模糊（≥0.75）

    ⚠️ PPT 文件名编号是**旧版编号**（如 `349.ppt` 是"救主正在等待"），与 DB 的新版编号
    并不一致（DB #349 = "奇妙的耶穌"），因此**只能靠标题匹配**，编号仅作参考。
    甲乙版本：DB 用 `51_a` / `51_b` 且标题带 `(甲)/(乙)` 后缀（如"萬古靈磐(甲)"），
    PPT 用 `51 (甲)、万古灵磐` —— 必须按版本标记消歧，否则 甲/乙 会互相串号。
    """
    out = {"hymn_number": "", "title_score": 0.0, "title_match": "none"}
    key = normalize_text(title)
    if not key:
        return out
    exact = index.get("titles", {}).get(key)
    if exact:
        out.update(hymn_number=exact[0], title_score=1.0,
                   title_match="exact" if len(exact) == 1 else "multi")
        return out
    best_no, best = "", 0.0
    for cand, nos in index.get("titles", {}).items():
        if not cand or len(nos) != 1:
            continue
        tail = cand[-1]
        if tail in CN_VER:                       # DB 标题带 (甲)/(乙) 版本后缀
            if ver and tail != ver:
                continue                         # 版本不符 → 直接排除（防 甲/乙 串号）
            base = cand[:-1]
            ratio = difflib.SequenceMatcher(None, key, base).ratio()
        else:
            ratio = difflib.SequenceMatcher(None, key, cand).ratio()
        if ratio > best:
            best_no, best = nos[0], ratio
    if best >= 0.75:
        out.update(hymn_number=best_no, title_score=round(best, 3), title_match="fuzzy")
    else:
        out.update(title_score=round(best, 3))
    return out


def resolve_by_lyrics(lyric_text, index):
    """标题不足以定编号时，用**歌词**反查：对全部正歌做覆盖率比对，取 ≥0.90 的最高者

    这是最硬的身份证据（PPT 单节歌词 ≈ DB 某节正歌），用于救回旧版/新版用字不同的诗
    （如 PPT「為主而活」vs DB「為主而活」异体、PPT「快親近神」vs DB「快親近主」）。
    返回 (hymn_number, coverage)；未达阈值返回 ("", 最佳覆盖率)。
    """
    target = normalize_text(lyric_text)
    if not target:
        return "", 0.0
    best_no, best = "", 0.0
    for no, verses in index.get("verses", {}).items():
        for verse in verses:
            if not verse:
                continue
            cov = 1.0 if target in verse else _coverage(target, verse)
            if cov > best:
                best_no, best = no, cov
                if cov >= 0.999:
                    return best_no, 1.0
    return (best_no, round(best, 3)) if best >= 0.90 else ("", round(best, 3))


def _coverage(target, reference):
    """target 中有多少比例能在 reference 里按序对上（对长度差异不敏感）

    PPT 单节歌词是 DB 正歌的一段（P=2 时只有一半），用比值会被长度稀释；
    覆盖率只问"这一节歌词是否确实出自该节正歌"，正好用于归属确认。
    """
    if not target or not reference:
        return 0.0
    sm = difflib.SequenceMatcher(None, target, reference, autojunk=False)
    matched = sum(block.size for block in sm.get_matching_blocks())
    return matched / len(target)


def match_verse(lyric_text, verses, hymn_number):
    """一节幻灯片的歌词串 → DB 正歌节号（1-based, 0=未匹配）+ 覆盖率

    歌词简繁已归一；先试包含（完全命中），否则取覆盖率最高的那节。
    """
    target = normalize_text(lyric_text)
    if not target or not hymn_number:
        return 0, 0.0
    best_no, best = 0, 0.0
    for idx, verse in enumerate(verses.get(hymn_number, []), start=1):
        if not verse:
            continue
        if target in verse:
            return idx, 1.0
        cov = _coverage(target, verse)
        if cov > best:
            best_no, best = idx, cov
    return best_no, round(best, 3)


def match_chorus(lyric_text, choruses, hymn_number):
    """副歌幻灯片（`(副歌)` 标签）→ DB `chorus` 字段覆盖率"""
    target = normalize_text(lyric_text)
    chorus = (choruses or {}).get(hymn_number, "")
    if not target or not chorus:
        return 0.0
    if target in chorus:
        return 1.0
    return round(_coverage(target, chorus), 3)


# ================= 五、单首 / 全量提取 =================

def _md5(path):
    """文件 md5（仅作来源溯源指纹，非安全用途）"""
    h = hashlib.md5()  # nosec B324 - 仅作来源溯源指纹，非安全用途
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_hymn_ppt(path, index=None):
    """解析单个 PPT → 结构化记录（元数据 + 每节每行 notes/lyric + 校验标志）

    校验项（写库并进报告，客户端据此决定是否直接使用）:
      title_match  exact / fuzzy / multi / none —— 标题→编号
      marker_ok    每节 `k/M` 与实际张数自洽（M == 张数 且 k == 序号）
      structure_ok 每节都有记号行，且每记号行紧跟歌词行（无孤行）
      tune_ok      曲调周期成立（每 P 张幻灯片旋律一致）
      verse_score  歌词与 DB 正歌相似度（≥0.75 视为归属确认）
      count_delta  行级：音符数 - 音节数（0=严格等长；>0 疑似一字多音；<0 需复核）
    """
    index = index or {"titles": {}, "verses": {}}
    atoms = [text for role, text in read_ppt_text(path)
             if role == RES_BODY and text.strip()]
    slides = [parse_slide(text, i + 1) for i, text in enumerate(atoms)]
    meta = next((s["header"] for s in slides if s.get("header")), None) or parse_header("")
    stem = os.path.splitext(os.path.basename(path))[0]
    suffix = re.sub(r"^\d+", "", stem)
    version = meta["ver"] or VER_FROM_SUFFIX.get(suffix.lower(), suffix)
    match = match_hymn_number(meta["title"], index, version)
    hymn_number = match["hymn_number"]
    verses = index.get("verses", {})
    if match["title_match"] in ("none", "multi"):
        for slide in slides[:3]:            # 用前 3 张幻灯片的歌词反查编号（身份最硬证据）
            cand, cov = resolve_by_lyrics("".join(p["lyric"] for p in slide["pairs"]), index)
            if cand:
                hymn_number = cand
                match = {"hymn_number": cand, "title_score": cov, "title_match": "lyric"}
                break

    lines, stanza_scores = [], []
    noise_bad, unmatched = [], []
    choruses = index.get("choruses", {})
    for slide in slides:
        stanza_text = "".join(p["lyric"] for p in slide["pairs"])
        label_chorus = slide["label_kind"] == "chorus"
        verse_no, verse_score = match_verse(stanza_text, verses, hymn_number)
        chorus_score = match_chorus(stanza_text, choruses, hymn_number)
        if label_chorus or (chorus_score > verse_score and chorus_score >= 0.75):
            is_chorus, verse_no, score = 1, 0, chorus_score
        else:
            is_chorus, score = 0, verse_score
        if score < 0.75:
            unmatched.append(f"节{slide['stanza_no']}({score:.2f}:{stanza_text[:14]})")
        stanza_scores.append(score)
        for pair in slide["pairs"]:
            n_notes = count_notes(pair["notes"])
            n_syll = count_syllables(pair["lyric"])
            noise = noise_chars(pair["notes"])
            if noise:
                noise_bad.append(f"第{slide['stanza_no']}节第{pair['line_no']}行 {'/'.join(noise)}")
            lines.append({
                "stanza_no": slide["stanza_no"], "line_no": pair["line_no"],
                "verse_no": verse_no, "is_chorus": is_chorus,
                "label": slide["label"], "notes": pair["notes"], "lyric": pair["lyric"],
                "note_count": n_notes, "rest_count": count_rests(pair["notes"]),
                "syllable_count": n_syll, "count_delta": n_notes - n_syll,
                "align_ok": 1 if n_notes >= n_syll > 0 else 0,
            })

    period, tune_ok = tune_period(slides)
    marker_ok = bool(slides) and all(
        s["marker_m"] == len(slides) and s["marker_k"] == s["stanza_no"] for s in slides)
    structure_ok = bool(slides) and all(s["pairs"] and not s["orphans"] for s in slides)
    verse_score = round(min(stanza_scores), 3) if stanza_scores else 0.0
    if not verses:
        verse_match = "n/a"
    elif verse_score >= 0.75:
        verse_match = "ok"
    elif verse_score >= 0.45:
        verse_match = "partial"
    else:
        verse_match = "fail"

    reasons = []
    if match["title_match"] in ("none", "multi"):
        reasons.append(f"标题→编号未定({match['title_match']})")
    elif match["title_match"] == "fuzzy" and verse_match != "ok":
        reasons.append(f"标题模糊匹配({match['title_score']})")
    if not marker_ok:
        reasons.append("节号 k/M 不自洽")
    if not structure_ok:
        reasons.append("记号行/歌词行结构异常")
    if not tune_ok:
        reasons.append("跨节曲调不一致")
    if verse_match in ("fail", "partial"):
        reasons.append(f"歌词归属未确认({verse_match}: {'、'.join(unmatched[:4])})")
    short = [ln for ln in lines if ln["count_delta"] < 0]
    if short:
        reasons.append(f"{len(short)} 行音符数少于字数")
    if noise_bad:
        reasons.append(f"{len(noise_bad)} 行含噪声字符")

    return {
        "ppt_file": os.path.basename(path), "ppt_old_no": meta["old_no"],
        "ppt_new_no": meta["new_no"], "title": meta["title"],
        "version": version,
        "title_line": meta["title_line"], "key_sig": meta["key_sig"],
        "time_sig": meta["time_sig"], "tempo": meta["tempo"],
        "hymn_number": hymn_number, "title_match": match["title_match"],
        "title_score": match["title_score"], "slide_count": len(slides),
        "chorus_slides": len({ln["stanza_no"] for ln in lines if ln["is_chorus"]}),
        "pair_count": len(lines), "note_total": sum(ln["note_count"] for ln in lines),
        "tune_period": period, "tune_ok": int(tune_ok),
        "marker_ok": int(marker_ok), "structure_ok": int(structure_ok),
        "verse_match": verse_match, "verse_score": verse_score,
        "align_ok": 0 if reasons else 1, "review_reason": "；".join(reasons),
        "src_md5": _md5(path), "extractor": EXTRACTOR, "lines": lines,
    }


def extract_all(ppt_dir=PPT_DIR, db_path=DB_PATH, only=None):
    """批量解析目录内全部 `*.ppt` → (records, stats)

    only: 可选编号集合（`{"349"}` / `{"349.ppt"}`），仅解析指定文件（抽样复核用）。
    """
    if not os.path.isdir(ppt_dir):
        raise SystemExit(f"❌ PPT 目录不存在：{ppt_dir}（大字库资源说明见 README）")
    index = load_db_index(db_path)
    files = sorted(f for f in os.listdir(ppt_dir) if f.lower().endswith(".ppt"))
    if only:
        want = {str(o).lower().removesuffix(".ppt") for o in only}
        files = [f for f in files if f[:-4].lower() in want]

    records = []
    for name in files:
        try:
            records.append(parse_hymn_ppt(os.path.join(ppt_dir, name), index))
        except (ValueError, OSError, RuntimeError, KeyError) as exc:  # 单文件异常不阻断全量
            records.append({
                "ppt_file": name, "hymn_number": "", "title": "", "title_line": "",
                "version": "", "ppt_old_no": "", "ppt_new_no": "", "key_sig": "",
                "time_sig": "", "tempo": "",
                "title_match": "error", "title_score": 0.0, "slide_count": 0, "pair_count": 0,
                "note_total": 0, "tune_period": 0, "tune_ok": 0, "marker_ok": 0,
                "structure_ok": 0, "verse_match": "n/a", "verse_score": 0.0, "align_ok": 0,
                "review_reason": f"解析失败：{exc}", "src_md5": "", "extractor": EXTRACTOR,
                "parse_error": str(exc), "lines": [],
            })
    return records, summarize(records)


def summarize(records):
    """统计校验结果（写报告 + 打印）"""
    lines = [ln for r in records for ln in r.get("lines", [])]

    def cnt(pred):
        return sum(1 for r in records if pred(r))

    def lcnt(pred):
        return sum(1 for ln in lines if pred(ln))

    return {
        "files": len(records),
        "parse_error": cnt(lambda r: r.get("parse_error")),
        "usable": cnt(lambda r: r.get("align_ok") == 1),
        "review": cnt(lambda r: r.get("align_ok") != 1),
        "mapped": cnt(lambda r: r.get("hymn_number")),
        "title_exact": cnt(lambda r: r.get("title_match") == "exact"),
        "title_fuzzy": cnt(lambda r: r.get("title_match") == "fuzzy"),
        "title_lyric": cnt(lambda r: r.get("title_match") == "lyric"),
        "title_multi": cnt(lambda r: r.get("title_match") == "multi"),
        "title_none": cnt(lambda r: r.get("title_match") in ("none", "error")),
        "marker_ok": cnt(lambda r: r.get("marker_ok") == 1),
        "structure_ok": cnt(lambda r: r.get("structure_ok") == 1),
        "tune_ok": cnt(lambda r: r.get("tune_ok") == 1),
        "verse_ok": cnt(lambda r: r.get("verse_match") == "ok"),
        "verse_partial": cnt(lambda r: r.get("verse_match") == "partial"),
        "verse_fail": cnt(lambda r: r.get("verse_match") == "fail"),
        "lines": len(lines),
        "lines_equal": lcnt(lambda ln: ln["count_delta"] == 0),
        "lines_melisma": lcnt(lambda ln: ln["count_delta"] > 0),
        "lines_short": lcnt(lambda ln: ln["count_delta"] < 0),
        "notes": sum(ln["note_count"] for ln in lines),
    }


# ================= 六、报告 =================

def build_report(records, stats, sample=12):
    """生成校验报告文本（先总览、再清单；供人工复核用官方简谱 PDF/PNG 终审）"""
    out = [
        "=" * 74, "《赞美诗》PPT「带简谱文字歌词」提取校验报告", "=" * 74,
        (f"PPT 文件：{stats['files']}    解析失败：{stats['parse_error']}    "
         f"可直接使用(全项通过)：{stats['usable']}    需复核：{stats['review']}"),
        (f"编号匹配：精确 {stats['title_exact']} / 模糊 {stats['title_fuzzy']} / "
         f"歌词反查 {stats['title_lyric']} / 多义 {stats['title_multi']} / "
         f"未匹配 {stats['title_none']}    （已映射到 DB：{stats['mapped']}）"),
        (f"结构校验：节号自洽 {stats['marker_ok']} / 记号-歌词成对 {stats['structure_ok']} / "
         f"曲调跨节一致 {stats['tune_ok']}  （分母 = PPT 文件数）"),
        (f"歌词归属：确认 {stats['verse_ok']} / 部分 {stats['verse_partial']} / "
         f"未确认 {stats['verse_fail']}  （与 DB 正歌比对，阈值 0.75）"),
        (f"行级等长（音符数 vs 字数）：严格等长 {stats['lines_equal']} / "
         f"一字多音 {stats['lines_melisma']} / 音符不足 {stats['lines_short']}"
         f"   （共 {stats['lines']} 行 / {stats['notes']} 个音符）"),
        "",
        "说明：PPT 的「简谱行」与「歌词行」是作者手工用空格对齐的两行文本，",
        "      因此**逐字对位不具备严格数据保证**；本报告校验的是可验证的四项：",
        "      ① 行级配对（每个简谱行紧跟其歌词行）；② 节号 k/M 与实际张数自洽；",
        "      ③ 跨节曲调周期一致；④ 歌词与 DB 正歌逐字比对（确认归属）。",
        "      行级 count_delta>0 = 一字多音（正常），<0 = 音符数不足（必须人工复核）。",
        "",
    ]

    review = [r for r in records if r.get("align_ok") != 1]
    out.append(f"—— 需复核清单（{len(review)} 项，最多列 {sample} 项）——")
    for rec in review[:sample]:
        out.append(f"  {rec['ppt_file']}  编号={rec['hymn_number'] or '?'}  "
                   f"标题={rec['title'] or '?'}  → {rec['review_reason']}")
    if len(review) > sample:
        out.append(f"  …… 其余 {len(review) - sample} 项见 DB `hymn_jianpu.review_reason`")

    short_lines = [(r["ppt_file"], ln) for r in records for ln in r.get("lines", [])
                   if ln["count_delta"] < 0]
    out.append("")
    out.append(f"—— 音符数少于字数的行（{len(short_lines)} 行，最多列 {sample} 行）——")
    for name, ln in short_lines[:sample]:
        out.append(f"  {name} 第{ln['stanza_no']}节第{ln['line_no']}行  "
                   f"音符 {ln['note_count']} < 字数 {ln['syllable_count']}")
        out.append(f"      记号: {ln['notes']}")
        out.append(f"      歌词: {ln['lyric']}")
    return out


def write_report(path=None, records=None, stats=None):
    """把校验报告写到 `data/jianpu_report.txt`（返回报告行数）"""
    path = path or REPORT_PATH
    lines = build_report(records or [], stats or {})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(lines)


def main(argv=None):
    """仅提取 + 出报告（不写库）；写库入口见 `tool/extract_jianpu.py`"""
    import argparse
    parser = argparse.ArgumentParser(description="提取 PPT 带简谱歌词并生成校验报告（不写库）")
    parser.add_argument("--ppt-dir", default=PPT_DIR, help=f"PPT 目录（默认 {PPT_DIR}）")
    parser.add_argument("--db", default=DB_PATH, help=f"数据库（默认 {DB_PATH}）")
    parser.add_argument("--report", default=REPORT_PATH, help=f"报告输出（默认 {REPORT_PATH}）")
    parser.add_argument("--only", nargs="*", default=None, help="仅解析指定编号，如 1 5 349")
    args = parser.parse_args(argv)
    records, stats = extract_all(args.ppt_dir, args.db, args.only)
    write_report(args.report, records, stats)
    print("\n".join(build_report(records, stats)[:9]))
    print(f"📄 报告已写入 {args.report}（共 {stats['files']} 个 PPT）")
    return stats


if __name__ == "__main__":
    main()
