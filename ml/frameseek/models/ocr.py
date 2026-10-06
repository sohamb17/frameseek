"""On-screen text recognition with Tesseract.

Raw word boxes and confidences are preserved per frame; only words above the
configured confidence are used for the searchable OCR text.
"""
from __future__ import annotations

import re

from PIL import Image, ImageOps

_WORDISH = re.compile(r"[A-Za-z0-9]")


def ocr_image(img: Image.Image, min_conf: float) -> dict:
    import pytesseract

    gray = ImageOps.grayscale(img)
    # Tesseract works best when glyphs are ~30px tall; upscale small frames.
    if gray.width < 1600:
        scale = 1600 / gray.width
        gray = gray.resize((1600, int(gray.height * scale)), Image.BICUBIC)
    else:
        scale = 1.0
    data = pytesseract.image_to_data(gray, config="--psm 3", output_type=pytesseract.Output.DICT)
    lines: dict[tuple, dict] = {}
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        conf = float(data["conf"][i])
        if not word or conf < min_conf or len(_WORDISH.findall(word)) < 2:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        box = [int(data["left"][i] / scale), int(data["top"][i] / scale),
               int(data["width"][i] / scale), int(data["height"][i] / scale)]
        line = lines.setdefault(key, {"words": [], "confs": [], "box": box})
        line["words"].append(word)
        line["confs"].append(conf)
        x, y, w, h = line["box"]
        x2, y2 = max(x + w, box[0] + box[2]), max(y + h, box[1] + box[3])
        x, y = min(x, box[0]), min(y, box[1])
        line["box"] = [x, y, x2 - x, y2 - y]
    out_lines = [{"text": " ".join(l["words"]), "conf": round(sum(l["confs"]) / len(l["confs"]), 1), "box": l["box"]}
                 for l in lines.values()]
    text = "\n".join(l["text"] for l in out_lines)
    n_words = sum(len(l["text"].split()) for l in out_lines)
    mean_conf = round(sum(l["conf"] for l in out_lines) / len(out_lines), 1) if out_lines else None
    return {"text": text, "lines": out_lines, "n_words": n_words, "conf": mean_conf}
