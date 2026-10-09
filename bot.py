#!/usr/bin/env python3
"""iNat 維多利亞追蹤器的 Telegram 指令 bot(公開 repo)。

這個 repo 只放轉接程式,不放任何資料:
- 追蹤器的程式與設定在私有 repo(TRACKER_REPO);工作流程執行時才用權杖把它抓到 tracker/ 目錄。
- 每次被觸發時:收 Telegram 的新訊息 → 交給 tracker.handle_update 處理並回覆
  → 若追蹤清單、暫停或靜音有變動,寫回私有 repo 的 config.json → 最後才向 Telegram 確認已讀。
- 不保存任何狀態;某次執行被跳過或失敗,沒確認的訊息會在下一次一起處理。

環境變數: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRACKER_TOKEN(私有 repo 的 Contents 讀寫權杖), HOME_LAT, HOME_LNG
"""
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tracker"))
import tracker  # noqa: E402  由工作流程從私有 repo 抓進來

# 從 GitHub 密鑰複製貼上時常多帶到空白或換行,會讓網址無效(InvalidURL);一律去掉頭尾空白
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_READONLY_CHAT_IDS", "TRACKER_TOKEN", "HOME_LAT", "HOME_LNG"):
    if _k in os.environ:
        os.environ[_k] = os.environ[_k].strip()
# Telegram token 的格式是「數字:英數字」;從密鑰內容中找出這一段,忽略一起貼進來的說明文字、空白或換行
_m = re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
TOKEN_FORMAT_OK = bool(_m)
if _m:
    os.environ["TELEGRAM_BOT_TOKEN"] = _m.group(0)

REPO = os.environ.get("TRACKER_REPO", "TommyPai/inat-victoria-tracker")
GH_API = os.environ.get("GITHUB_API_BASE", "https://api.github.com")
TG_API = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")


def tg(method, **params):
    url = f"{TG_API}/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"
    return json.load(urllib.request.urlopen(url, urllib.parse.urlencode(params).encode(), timeout=30))


def gh(method, path, body=None):
    req = urllib.request.Request(
        f"{GH_API}/repos/{REPO}/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['TRACKER_TOKEN']}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "inat-telegram-bot"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def save_config(cfg, message):
    """把設定寫回私有 repo 的 config.json。帶上目前的 sha,若期間有別人改過會失敗而不是覆蓋。"""
    cur = gh("GET", "contents/config.json?ref=main")
    content = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    gh("PUT", "contents/config.json", {"message": message, "branch": "main", "sha": cur["sha"],
                                       "content": base64.b64encode(content.encode("utf-8")).decode()})


def prune_controls(controls):
    """清掉已過期的暫停與靜音,保持 config 乾淨。"""
    now = datetime.now(timezone.utc)
    pu = tracker.parse_iso(controls.get("pause_until"))
    if pu and pu <= now:
        controls.pop("pause_until")
    mutes = controls.get("mutes") or {}
    controls["mutes"] = {k: v for k, v in mutes.items() if tracker.parse_iso(v) and tracker.parse_iso(v) > now}
    if not controls["mutes"]:
        controls.pop("mutes")


def main():
    chat = os.environ["TELEGRAM_CHAT_ID"]
    cfg = tracker.load_json(tracker.CONFIG_PATH, None)
    before = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    tracker.HOME = tracker.load_home(cfg)  # 家的座標只從密鑰讀取
    controls = cfg.setdefault("controls", {})
    cfg["commands_via_public_bot"] = True  # 私有 repo 的定時檢查從此不再自己收 Telegram 訊息,避免兩邊搶訊息
    if cfg.get("bot_menu_version") != tracker.BOT_COMMANDS_VERSION:
        try:
            tracker.set_bot_commands(os.environ["TELEGRAM_BOT_TOKEN"])
            cfg["bot_menu_version"] = tracker.BOT_COMMANDS_VERSION
        except Exception as e:
            print(f"設定指令選單失敗: {type(e).__name__}", file=sys.stderr)

    updates = tg("getUpdates", timeout=0).get("result", [])
    for u in updates:
        tracker.handle_update(u, cfg, controls, chat)

    prune_controls(controls)
    if not controls:
        cfg.pop("controls")
    if json.dumps(cfg, sort_keys=True, ensure_ascii=False) != before:
        save_config(cfg, "bot: update config from Telegram")
    if updates:  # 全部處理完、設定也存好之後才確認已讀
        tg("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0, limit=1)
    print(f"處理了 {len(updates)} 則更新")  # 公開 repo 的執行紀錄是公開的,這裡只印數量


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 公開 repo 的執行紀錄任何人都看得到: 只印錯誤類型,不印內容或追蹤細節
        print(f"執行失敗: {type(e).__name__}", file=sys.stderr)
        if not TOKEN_FORMAT_OK:  # 只印長度與格式判斷,絕不印出內容
            raw = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            print(f"提示: TELEGRAM_BOT_TOKEN 密鑰裡找不到「數字:英數字」格式的 token(密鑰長度 {len(raw)} 個字元)", file=sys.stderr)
        sys.exit(1)
