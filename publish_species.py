#!/usr/bin/env python3
"""從私有 repo 的 config.json 產生地圖網頁用的物種清單 docs/species.json。

只輸出地圖需要的公開資訊(taxon_id、學名、中文名、類群),不含任何設定、聊天室或座標。
內容沒變就不改檔案,避免每次執行都產生 commit。
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "tracker"))
import tracker  # noqa: E402  由工作流程從私有 repo 抓進來


def split_label(label):
    """'Anoplognathus (聖誕金龜) [genus]' → ('Anoplognathus', '聖誕金龜', 'genus')"""
    rank = (re.findall(r"\[([^\]]*)\]", label) or [""])[0]
    zh = (re.findall(r"\(([^)]*)\)", label) or [""])[0]
    sci = re.split(r" \(| \[", label)[0].strip()
    return sci, zh, rank


def main():
    cfg = tracker.load_json(tracker.CONFIG_PATH, None)
    groups = sorted({s.get("group") or "其他" for s in cfg["species"]}, key=lambda g: tracker.group_order(cfg, g))
    species = []
    for s in cfg["species"]:
        sci, zh, rank = split_label(s.get("label") or str(s["taxon_id"]))
        species.append({"taxon_id": s["taxon_id"], "name": sci, "zh": zh, "rank": rank, "group": s.get("group") or "其他"})
    out = {"place_id": cfg["place_id"], "quality_grades": cfg.get("quality_grades") or ["research", "needs_id"],
           "groups": [{"name": g, "icon": tracker.GROUP_ICONS.get(g, "▪️")} for g in groups], "species": species}
    path = HERE / "docs" / "species.json"
    text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
        print(f"已更新物種清單: {len(species)} 個")
    else:
        print("物種清單沒有變動")


if __name__ == "__main__":
    main()
