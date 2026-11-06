#!/usr/bin/env python3
"""為地圖的「Tea-tree in flower」圖層準備候選紀錄與照片總表。

流程:Claude 的每週排程任務把日期範圍寫進 teatree/request.json 推到 main
→ 這支程式(.github/workflows/teatree.yml)抓 iNaturalist 那段期間維多利亞州的野生茶樹與訪花甲蟲紀錄,
  下載縮圖、拼成有編號的照片總表 → 結果以單一 commit 強制推到 teatree-work 分支
→ Claude 看照片判讀,把確定有開花的寫進 docs/teatree.json。
(Claude 的雲端環境連不到 iNaturalist,GitHub 連得到,所以抓資料這段放在這裡。)

輸出(out/):candidates.json、sheets/plant-01.jpg…、sheets/beetle-01.jpg…
"""
import io
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

API = "https://api.inaturalist.org/v1/observations"
UA = {"User-Agent": "inat-victoria-bot teatree-prep (github.com/TommyPai/inat-victoria-bot)"}
GROUPS = {"plant": "54708,1546108",                 # Leptospermum、Gaudium
          "beetle": "54964,55051,81969,201852"}     # 吉丁蟲、郭公蟲、花蚤、Phyllotocus
CELL, COLS, ROWS = 150, 8, 6                        # 每張總表 8×6 = 48 格
OUT = Path("out")


def get_json(url, tries=4):
    for n in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except Exception:  # iNat 偶爾回 429/5xx
            if n == tries - 1:
                raise
            time.sleep(5 * (n + 1))


def fetch(taxa, d1, d2):
    out, below = [], None
    for _ in range(30):
        p = {"taxon_id": taxa, "place_id": 7830, "d1": d1, "d2": d2, "captive": "false", "photos": "true",
             "per_page": 200, "order_by": "id", "order": "desc"}
        if below:
            p["id_below"] = below
        res = get_json(f"{API}?{urllib.parse.urlencode(p)}")["results"]
        out += res
        if len(res) < 200:
            break
        below = res[-1]["id"]
        time.sleep(1.2)
    return out


def thumb(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            im = Image.open(io.BytesIO(r.read())).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    s = min(w, h)
    return im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((CELL, CELL))


def sheets(kind, items):
    font = ImageFont.load_default(size=20)
    names = []
    per = COLS * ROWS
    with ThreadPoolExecutor(6) as ex:
        thumbs = list(ex.map(lambda c: thumb(c["photo_small"]), items))
    for k in range(0, len(items), per):
        chunk = list(zip(items[k:k + per], thumbs[k:k + per]))
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * CELL + (COLS - 1) * 3, rows * CELL + (rows - 1) * 3), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for j, (c, im) in enumerate(chunk):
            x, y = (j % COLS) * (CELL + 3), (j // COLS) * (CELL + 3)
            if im:
                sheet.paste(im, (x, y))
            else:
                d.text((x + 30, y + 60), "no photo", fill=(200, 200, 200), font=font)
            label = str(c["i"])
            tw = d.textlength(label, font=font)
            d.rectangle((x, y, x + tw + 8, y + 24), fill=(0, 0, 0))
            d.text((x + 4, y + 1), label, fill=(255, 255, 255), font=font)
        name = f"sheets/{kind}-{k // per + 1:02d}.jpg"
        sheet.save(OUT / name, quality=88)
        names.append(name)
    return names


def main():
    req = json.loads(Path("teatree/request.json").read_text()) if Path("teatree/request.json").exists() else {}
    mel = datetime.now(timezone(timedelta(hours=11)))
    d2 = req.get("d2") or mel.date().isoformat()
    d1 = req.get("d1") or (mel.date() - timedelta(days=6)).isoformat()
    (OUT / "sheets").mkdir(parents=True, exist_ok=True)
    cands, sheet_names, i = [], {}, 0
    for kind, taxa in GROUPS.items():
        items = []
        for o in fetch(taxa, d1, d2):
            ph = (o.get("photos") or [{}])[0].get("url")
            if not o.get("location") or not ph:
                continue
            la, lo = map(float, o["location"].split(","))
            items.append({"i": i, "kind": kind, "id": o["id"], "date": o.get("observed_on"), "lat": round(la, 4), "lng": round(lo, 4),
                          "taxon": (o.get("taxon") or {}).get("name", ""), "place": (o.get("place_guess") or "").split(",")[0].strip(),
                          "obscured": bool(o.get("obscured")), "photo": ph.replace("square", "medium"), "photo_small": ph.replace("square", "small")})
            i += 1
        sheet_names[kind] = sheets(kind, items)
        cands += items
    (OUT / "candidates.json").write_text(json.dumps({
        "d1": d1, "d2": d2, "requested": req.get("requested", ""), "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cells": f"{COLS} columns x {ROWS} rows per sheet, number in the top-left corner = candidate index i",
        "sheets": sheet_names, "candidates": cands}, ensure_ascii=False, indent=0))
    print(f"{d1}..{d2}: {sum(c['kind'] == 'plant' for c in cands)} tea-tree, {sum(c['kind'] == 'beetle' for c in cands)} beetle records")


if __name__ == "__main__":
    main()
