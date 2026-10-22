# iNat Victoria Telegram bot

處理 [iNat 維多利亞追蹤器](https://github.com/TommyPai/inat-victoria-tracker)(私有)的 Telegram 指令,讓查詢在幾分鐘內回覆。

- 這個 repo 只有轉接程式,沒有任何資料或設定;追蹤器的程式與設定在執行時才從私有 repo 取得。
- 由外部定時服務每 2 分鐘以 `workflow_dispatch` 觸發。公開 repo 的 Actions 不計入分鐘額度。
- 需要的 secrets:`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`TRACKER_TOKEN`(只限私有 repo、Contents 讀寫的 fine-grained token)、`HOME_LAT`、`HOME_LNG`。
- 使用的 GitHub Actions 都鎖定到特定 commit,執行紀錄只印處理數量與錯誤類型。

## 即時回覆(選用,Cloudflare webhook)

預設每 2 分鐘去 Telegram 取一次訊息。啟用 webhook 後,Telegram 一收到訊息就推給 Cloudflare Worker([cloudflare/worker.js](cloudflare/worker.js)),Worker 存進 D1 並立刻觸發這個工作流程,回覆時間縮短到約 20–40 秒(主要是 GitHub 開機器的時間)。

1. Cloudflare(免費方案)建立 Worker,貼上 `cloudflare/worker.js`;建立一個 D1 資料庫並綁定到 Worker,變數名稱 `DB`(資料表會自動建立)。
2. Worker 加三個 Secret:`TG_WEBHOOK_SECRET`、`QUEUE_SECRET`(兩個隨機長字串)、`GH_TOKEN`(只限這個 repo、Actions 讀寫的 fine-grained token)。
3. 這個 repo 加三個 secrets:`TG_QUEUE_URL`(Worker 網址)、`TG_QUEUE_SECRET`、`TG_WEBHOOK_SECRET`(和 Worker 的相同)。
4. 下一次執行時 bot 會自動向 Telegram 設定 webhook;之後定時觸發只是備援。刪掉 `TG_QUEUE_URL` 就會自動關閉 webhook、改回輪詢。
