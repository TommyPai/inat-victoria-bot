#!/usr/bin/env python3
"""iNat 維多利亞追蹤器的 Telegram 指令 bot(公開 repo)。

這個 repo 只放轉接程式,不放任何資料:
- 追蹤器的程式與設定在私有 repo(TRACKER_REPO);工作流程執行時才用權杖把它抓到 tracker/ 目錄。
- 每次被觸發時:收 Telegram 的新訊息 → 交給 tracker.handle_update 處理並回覆
  → 若追蹤清單、暫停或靜音有變動,寫回私有 repo 的 config.json → 最後才向 Telegram 確認已讀。
- 不保存任何狀態;某次執行被跳過或失敗,沒確認的訊息會在下一次一起處理。
- 有設定 TG_QUEUE_URL 時改用 webhook 模式: Telegram 把訊息推到 Cloudflare Worker(cloudflare/worker.js),
  Worker 存起來並立刻觸發這個工作流程;這裡改向 Worker 取訊息與確認。拿掉這個設定就自動改回 getUpdates。

環境變數: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRACKER_TOKEN(私有 repo 的 Contents 讀寫權杖), HOME_LAT, HOME_LNG,
         webhook 模式另需 TG_QUEUE_URL, TG_QUEUE_SECRET, TG_WEBHOOK_SECRET
"""
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tracker"))
import tracker  # noqa: E402  由工作流程從私有 repo 抓進來

# 從 GitHub 密鑰複製貼上時常多帶到空白或換行,會讓網址無效(InvalidURL);一律去掉頭尾空白
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_READONLY_CHAT_IDS", "TRACKER_TOKEN", "HOME_LAT", "HOME_LNG",
           "TG_QUEUE_URL", "TG_QUEUE_SECRET", "TG_WEBHOOK_SECRET"):
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
QUEUE_URL = os.environ.get("TG_QUEUE_URL", "").rstrip("/")  # 空的 = 沒啟用 webhook,照舊用 getUpdates


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


def queue(method, path, body=None):
    """向 Cloudflare Worker 取訊息(GET /updates)或確認已處理(POST /ack)。"""
    req = urllib.request.Request(
        f"{QUEUE_URL}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['TG_QUEUE_SECRET']}", "Content-Type": "application/json",
                 "User-Agent": "inat-telegram-bot"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def sync_webhook(cfg):
    """讓 Telegram 的 webhook 設定跟 TG_QUEUE_URL 一致: 有設定就指向 Worker,拿掉就關掉 webhook 改回 getUpdates。
    config 只記網址的雜湊,不記網址本身。設定 webhook 時 Telegram 會把還沒取走的訊息轉推給 Worker,不會掉訊息。"""
    want = hashlib.sha256(QUEUE_URL.encode()).hexdigest()[:12] if QUEUE_URL else None
    if cfg.get("webhook") == want:
        return
    if want:
        tg("setWebhook", url=f"{QUEUE_URL}/telegram", secret_token=os.environ["TG_WEBHOOK_SECRET"],
           allowed_updates=json.dumps(["message", "callback_query"]))
        cfg["webhook"] = want
    else:
        tg("deleteWebhook")
        cfg.pop("webhook", None)


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

    try:
        sync_webhook(cfg)
        updates = (queue("GET", "/updates") if QUEUE_URL else tg("getUpdates", timeout=0)).get("result", [])
    except (urllib.error.URLError, TimeoutError) as e:
        # Telegram / Worker 暫時連不上或沒回應: 這時還沒處理、也沒確認任何訊息,下一次執行會原封不動再處理,
        # 所以只留警告、不算失敗(避免每次網路抖動都寄失敗通知)。token 或密鑰錯誤等 4xx 仍照常失敗。
        if isinstance(e, urllib.error.HTTPError) and e.code < 500:
            raise
        print(f"::warning::{'Worker' if QUEUE_URL else 'Telegram'} 暫時連不上({type(e).__name__}),下一次執行會自動重試")
        if json.dumps(cfg, sort_keys=True, ensure_ascii=False) != before:  # webhook 已切換成功的話,記下來
            save_config(cfg, "bot: update config from Telegram")
        return
    for u in updates:
        tracker.handle_update(u, cfg, controls, chat)

    prune_controls(controls)
    if not controls:
        cfg.pop("controls")
    if json.dumps(cfg, sort_keys=True, ensure_ascii=False) != before:
        save_config(cfg, "bot: update config from Telegram")
    if updates:  # 全部處理完、設定也存好之後才確認已讀
        offset = updates[-1]["update_id"] + 1
        queue("POST", "/ack", {"offset": offset}) if QUEUE_URL else tg("getUpdates", offset=offset, timeout=0, limit=1)
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
