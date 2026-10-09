# iNat Victoria Telegram bot

處理 [iNat 維多利亞追蹤器](https://github.com/TommyPai/inat-victoria-tracker)(私有)的 Telegram 指令,讓查詢在幾分鐘內回覆。

- 這個 repo 只有轉接程式,沒有任何資料或設定;追蹤器的程式與設定在執行時才從私有 repo 取得。
- 由外部定時服務每 2 分鐘以 `workflow_dispatch` 觸發。公開 repo 的 Actions 不計入分鐘額度。
- 需要的 secrets:`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`TRACKER_TOKEN`(只限私有 repo、Contents 讀寫的 fine-grained token)、`HOME_LAT`、`HOME_LNG`。
- 使用的 GitHub Actions 都鎖定到特定 commit,執行紀錄只印處理數量與錯誤類型。
