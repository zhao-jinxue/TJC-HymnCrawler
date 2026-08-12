#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调用 Qwen-VL-OCR（DashScope OpenAI 兼容接口）识别目录图片中的文字。

- API Key 从本地文件读取（默认 /home/zjx/.cline/qwen_key.txt），脚本内部使用，不打印。
- 图片目录默认 /mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录
- 识别结果：每张图输出一个 .txt 到 <图片目录>/ocr_output/，并在终端打印。
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_KEY_FILE = "/home/zjx/.cline/qwen_key.txt"
DEFAULT_IMG_DIR = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录"
DEFAULT_MODEL = "qwen-vl-ocr"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

OCR_PROMPT = (
    "请识别这张图片中的所有文字内容，严格保留原始排版结构（层级、换行、缩进）。"
    "这是赞美诗分类目录图片，请按目录层级输出：大类 -> 小类 -> 诗名(编号)。"
    "仅输出识别到的文字，不要添加任何解释或评论。"
)


def load_api_key(key_file: str) -> str:
    """从本地文件读取 API Key，不做任何回显。"""
    if not os.path.isfile(key_file):
        sys.exit(f"[错误] API Key 文件不存在: {key_file}")
    with open(key_file, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read().strip()
    # 兼容 "export DASHSCOPE_API_KEY=sk-xxx" / "DASHSCOPE_API_KEY=sk-xxx" / "sk-xxx" 等格式
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            line = line.split("=", 1)[1]
        line = line.strip().strip('"').strip("'")
        if line:
            return line
    sys.exit("[错误] API Key 文件为空或格式无法解析")


def encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def ocr_image(api_key: str, model: str, img_path: str, timeout: int = 120,
              prompt: str = OCR_PROMPT) -> str:
    b64 = encode_image(img_path)
    ext = os.path.splitext(img_path)[1].lstrip(".").lower() or "jpeg"
    mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        sys.exit(f"[HTTP {e.code}] 模型 {model} 调用失败:\n{body[:1500]}")
    except urllib.error.URLError as e:
        sys.exit(f"[网络错误] {e}")

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        sys.exit(f"[解析失败] 响应结构异常:\n{json.dumps(data, ensure_ascii=False)[:1500]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Qwen-VL-OCR 图片文字识别")
    parser.add_argument("--img-dir", default=DEFAULT_IMG_DIR, help="图片目录路径")
    parser.add_argument("--key-file", default=DEFAULT_KEY_FILE, help="API Key 文件路径")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="模型名称")
    parser.add_argument("--pattern", default="*.jpg", help="图片匹配模式（默认 *.jpg）")
    args = parser.parse_args()

    api_key = load_api_key(args.key_file)

    img_dir = os.path.abspath(args.img_dir)
    if not os.path.isdir(img_dir):
        sys.exit(f"[错误] 图片目录不存在: {img_dir}")

    # 匹配图片文件（扩展名 jpg/jpeg/png）
    exts = {".jpg", ".jpeg", ".png"}
    files = sorted(
        f for f in os.listdir(img_dir)
        if os.path.splitext(f)[1].lower() in exts
    )
    if not files:
        sys.exit(f"[错误] 目录中没有图片文件: {img_dir}")

    out_dir = os.path.join(img_dir, "ocr_output")
    os.makedirs(out_dir, exist_ok=True)

    print(f"模型: {args.model}")
    print(f"图片目录: {img_dir}")
    print(f"待识别图片: {len(files)} 张\n")

    results = {}
    for i, name in enumerate(files, 1):
        img_path = os.path.join(img_dir, name)
        print(f"[{i}/{len(files)}] 识别中: {name} ...", flush=True)
        try:
            text = ocr_image(api_key, args.model, img_path)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"  !! 识别失败: {e}", flush=True)
            results[name] = None
            continue

        results[name] = text
        txt_name = os.path.splitext(name)[0] + ".txt"
        txt_path = os.path.join(out_dir, txt_name)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"  已保存 -> {txt_path}", flush=True)

    print("\n===== 识别结果摘要 =====")
    ok = sum(1 for v in results.values() if v)
    print(f"成功: {ok}/{len(files)}")

    summary_path = os.path.join(out_dir, "_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        for name, text in results.items():
            f.write(f"===== {name} =====\n")
            f.write((text or "[识别失败]") + "\n\n")
    print(f"汇总文件: {summary_path}")


if __name__ == "__main__":
    main()