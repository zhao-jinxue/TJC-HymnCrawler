#!/usr/bin/env python3
"""
crawler_core/images.py — PDF 转窄边距 PNG + 双页拼接（原 step5_pdf2png.py）

职责:
  - 递归扫描 Hymn_Downloads/ 下所有 .pdf
  - pdftoppm 300DPI 转 PNG(单页为同名 .png; 多页先转 name_p1/p2.png)
  - 自动检测内容包围盒, 裁掉四周空白, 保留窄边距
  - 多页(双页)PDF: 将 _p1.png(上) + _p2.png(下) 上下拼接为同名 .png, 并删除分页小图

用法:
  python3 -m crawler_core.images                # 增量: 仅转换 PNG 不存在 或 PDF 更新的
  python3 -m crawler_core.images --force        # 全量重新转换并覆盖
  python3 -m crawler_core.images --reset-progress  # 清空进度文件, 配合 --force 全量重做
  python3 -m crawler_core.images --limit 3      # 只处理前 N 个(测试用)
  from crawler_core.images import run           # 程序化入口

断点续跑（中间文件）:
  - 进度文件: Hymn_Downloads/step5_progress.json
    记录已完成转换的 PDF 相对路径; --reset-progress + --force 全量重做
"""
import argparse
import json
import os
import subprocess  # nosec B404 - 仅调用固定系统命令(pdftoppm/pdfinfo), 非用户输入拼接

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

DEFAULT_DPI = 300
DEFAULT_MARGIN = 40
WHITE_THRESHOLD = 245  # 低于该灰度值视为内容
PROGRESS_FILE = os.path.join("Hymn_Downloads", "step5_progress.json")
PROGRESS_FLUSH_EVERY = 20  # 每 N 个新增完成项落盘一次(防中断丢进度)
PROGRESS_VERSION = 1


def load_progress():
    """加载进度: {相对路径: 状态}"""
    if not os.path.exists(PROGRESS_FILE):
        return {}
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != PROGRESS_VERSION:
            return {}
        return {p: "done" for p in data.get("completed", [])}
    except Exception:  # noqa: BLE001 - 进度文件损坏时从头开始
        return {}


def save_progress(completed_list):
    """写进度文件"""
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": PROGRESS_VERSION, "completed": completed_list},
                  fh, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)  # 原子替换, 防中断损坏


def reset_progress():
    """清空进度文件"""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("已清空进度文件")


def find_all_pdfs(base_dir):
    """递归收集所有 PDF 文件路径"""
    result = []
    for dirpath, _dirnames, filenames in os.walk(base_dir):
        for fn in sorted(filenames):
            if fn.lower().endswith(".pdf"):
                result.append(os.path.join(dirpath, fn))
    return sorted(result)


def get_page_count(pdf_path):
    """用 pdfinfo 获取页数"""
    r = subprocess.run(  # nosec B603, B607 - 固定命令 pdfinfo + 参数化参数(非 shell 拼接, 无注入面)
        ["pdfinfo", pdf_path], capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        return 1
    for line in r.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 1


def trim_to_margin(png_path, margin):
    """裁掉四周白边, 保留 margin 像素窄边距; 返回原始/裁后尺寸"""
    from PIL import Image

    im = Image.open(png_path)
    w, h = im.size

    # 灰度 -> 内容(非白)置为255, 背景置为0 -> getbbox 快速求包围盒(C速度)
    gray = im.convert("L")
    mask = gray.point(lambda p: 255 if p < WHITE_THRESHOLD else 0)
    bbox = mask.getbbox()

    if bbox is None:
        return (w, h), (w, h)  # 全白页, 不裁剪

    left, top, right, bottom = bbox
    box = (
        max(0, left - margin),
        max(0, top - margin),
        min(w, right + margin),
        min(h, bottom + margin),
    )
    im2 = im.crop(box)
    im2.save(png_path, dpi=(im.info.get("dpi", (DEFAULT_DPI, DEFAULT_DPI))[0],) * 2)
    return (w, h), im2.size


def join_pages(dirpath, base):
    """双页拼接: <基名>_p1.png(上) + <基名>_p2.png(下) -> <基名>.png

    返回 (status, info):
      status: 'ok' 拼接成功 / 'skip' 无需拼接 / 'missing' 缺分页
    """
    p1 = os.path.join(dirpath, base + "_p1.png")
    p2 = os.path.join(dirpath, base + "_p2.png")
    out = os.path.join(dirpath, base + ".png")

    if not (os.path.exists(p1) and os.path.exists(p2)):
        return "missing", "缺分页图"

    # 增量: 整图比两个分页都新则跳过
    if os.path.exists(out):
        t_out = os.path.getmtime(out)
        if t_out >= os.path.getmtime(p1) and t_out >= os.path.getmtime(p2):
            return "skip", "拼接整图已是最新"

    from PIL import Image
    im1 = Image.open(p1).convert("RGB")
    im2 = Image.open(p2).convert("RGB")
    w = max(im1.width, im2.width)
    h = im1.height + im2.height
    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    canvas.paste(im1, ((w - im1.width) // 2, 0))
    canvas.paste(im2, ((w - im2.width) // 2, im1.height))
    canvas.save(out, dpi=(300, 300))
    im1.close()
    im2.close()

    # 拼接成功后删除分页小图
    for pg in (p1, p2):
        if os.path.exists(pg):
            os.remove(pg)
    return "ok", f"{w}x{h}"


def clean_orphan_pages(dirpath):
    """清理无同名整图的分页小图(防残留)"""
    files = set(os.listdir(dirpath))
    removed = 0
    for fn in list(files):
        if fn.endswith(("_p1.png", "_p2.png")):
            base = fn.replace("_p1.png", "").replace("_p2.png", "")
            if base + ".png" in files:
                os.remove(os.path.join(dirpath, fn))
                removed += 1
    return removed


def convert_one(pdf_path, dpi, margin, force):
    """转换单个 PDF; 返回 (状态, 说明)"""
    base = pdf_path[:-4]  # 去掉 .pdf
    dirpath = os.path.dirname(base)
    basename = os.path.basename(base)
    page_count = get_page_count(pdf_path)

    # 单页: 目标为该同名 .png; 双页: 目标为分页 _p1/_p2(后续拼接)
    if page_count == 1:
        targets = [base + ".png"]
    else:
        targets = [f"{base}_p{i}.png" for i in range(1, page_count + 1)]

    # 增量判断: 全部目标存在且比 PDF 新 -> 跳过
    pdf_mtime = os.path.getmtime(pdf_path)
    if not force:
        all_fresh = True
        for t in targets:
            if not os.path.exists(t) or os.path.getmtime(t) < pdf_mtime:
                all_fresh = False
                break
        if all_fresh:  # noqa: SIM102 - 两层条件语义不同, 保持清晰
            # 双页还需确认拼接整图存在
            if page_count == 1 or os.path.exists(base + ".png"):
                return "skip", "已存在且为最新"

    # pdftoppm 转换:
    #  单页 -> -singlefile 输出 base.png (不追加页码后缀)
    #  多页 -> 输出 base-1.png base-2.png ... 再重命名为 base_p1.png base_p2.png ...
    if page_count == 1:
        cmd = ["pdftoppm", "-png", "-r", str(dpi), "-singlefile", pdf_path, base]
    else:
        cmd = ["pdftoppm", "-png", "-r", str(dpi), pdf_path, base]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603 - 固定命令 pdftoppm + 参数化参数(非 shell 拼接, 无注入面)
    if r.returncode != 0:
        return "fail", "pdftoppm失败: " + r.stderr.strip()[:200]

    # 多页: 把 base-1.png/base-2.png 改名成 base_p1.png/base_p2.png
    if page_count > 1:
        for i in range(1, page_count + 1):
            src = f"{base}-{i}.png"
            dst = f"{base}_p{i}.png"
            if os.path.exists(src):
                os.replace(src, dst)
            elif not os.path.exists(dst):
                return "fail", f"未生成第{i}页 {os.path.basename(src)}"

    # 各页裁剪
    trim_info = []
    for t in targets:
        if not os.path.exists(t):
            return "fail", f"未生成目标 {os.path.basename(t)}"
        orig, new = trim_to_margin(t, margin)
        trim_info.append(f"{os.path.basename(t)} {orig[0]}x{orig[1]}->{new[0]}x{new[1]}")

    # 双页: 拼接整图 + 清理分页
    if page_count > 1:
        jstatus, jinfo = join_pages(dirpath, basename)
        if jstatus == "ok":
            trim_info.append(f"拼接 {jinfo}")
        elif jstatus == "fail":
            return "fail", "拼接失败"

    return "ok", "; ".join(trim_info)


def run(dpi=DEFAULT_DPI, margin=DEFAULT_MARGIN, force=False, limit=0, reset=False):
    """程序化入口: 供 crawler_fast.py 调用; 命令行入口走 main()"""
    if reset:
        reset_progress()

    pdfs = find_all_pdfs("Hymn_Downloads")
    total = len(pdfs)
    print(f"扫描到 PDF 总数: {total}")
    if total == 0:
        print("未找到 PDF, 退出")
        return {"ok": 0, "skip": 0, "fail": 0, "total": 0, "resume": 0}

    # 加载断点进度
    progress = load_progress()
    resume_skip = 0
    if progress and not force and limit == 0:
        # 增量模式下, 进度文件里已完成的自动跳过(不重复判断)
        pending = [p for p in pdfs if os.path.relpath(p) not in progress]
        resume_skip = len(pdfs) - len(pending)
        if resume_skip:
            print(f"断点续跑: 跳过 {resume_skip} 个进度文件中已完成的 PDF")
        pdfs = pending
        total = len(pdfs)

    if limit > 0:
        pdfs = pdfs[:limit]
        print(f"测试模式: 仅处理前 {limit} 个")
    else:
        print(f"模式: {'全量覆盖' if force else '增量(跳过已处理)'} | DPI={dpi} | 边距={margin}px")

    # 进度内集合(用于增量落盘)
    completed = list(progress.keys())
    dirty_since_flush = 0

    ok = fail = skip = 0
    fail_list = []
    for idx, pdf in enumerate(pdfs, 1):
        rel = os.path.relpath(pdf)
        # 已记录进度 -> 跳过(断点续跑)
        if rel in progress and not force:
            skip += 1
            continue

        status, detail = convert_one(pdf, dpi, margin, force)
        if status == "ok":
            ok += 1
            print(f"[{idx}/{total}] OK   {rel} | {detail}")
        elif status == "skip":
            skip += 1
            print(f"[{idx}/{total}] SKIP {rel}")
        else:
            fail += 1
            fail_list.append(rel)
            print(f"[{idx}/{total}] FAIL {rel} | {detail}")

        # OK 与 SKIP(已是最新) 都算完成, 记入进度
        if status in ("ok", "skip"):  # noqa: SIM102 - 嵌套条件便于阅读, 保持现状
            if rel not in completed:
                completed.append(rel)
                dirty_since_flush += 1
                if dirty_since_flush >= PROGRESS_FLUSH_EVERY:
                    save_progress(completed)
                    dirty_since_flush = 0

    # 最终落盘
    if dirty_since_flush > 0 and completed:
        save_progress(completed)
    if completed and os.path.exists(PROGRESS_FILE):
        print(f"断点进度已保存: {len(completed)} 个已完成")

    print("\n===== 统计 =====")
    print(f"总数: {total} | 成功: {ok} | 跳过: {skip} | 失败: {fail}")

    # 清理残留分页图(无论转换与否, 保证目录整洁)
    cleaned = 0
    for dirpath, _d, files in os.walk("Hymn_Downloads"):
        if any(f.endswith(("_p1.png", "_p2.png")) for f in files):
            cleaned += clean_orphan_pages(dirpath)
    if cleaned:
        print(f"清理残留分页小图: {cleaned} 张")

    if fail_list:
        print("失败清单:")
        for f in fail_list:
            print("  " + f)
    print("完成")
    return {"ok": ok, "skip": skip, "fail": fail, "total": total, "resume": resume_skip}


def main():
    parser = argparse.ArgumentParser(description="批量 PDF -> 窄边距 PNG")
    parser.add_argument("--force", action="store_true", help="全量重新转换并覆盖(进度文件仍生效)")
    parser.add_argument("--reset-progress", action="store_true", help="清空进度文件(配合 --force 全量重做)")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="分辨率(默认300)")
    parser.add_argument("--margin", type=int, default=DEFAULT_MARGIN, help="窄边距像素(默认40)")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个(测试用, 0=全部)")
    args = parser.parse_args()
    run(dpi=args.dpi, margin=args.margin, force=args.force, limit=args.limit, reset=args.reset_progress)


if __name__ == "__main__":
    main()