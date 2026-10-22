// iNat 維多利亞追蹤器的 Telegram webhook 轉接(Cloudflare Worker,免費方案即可)。
//
// Telegram 一收到訊息就推到這裡 → 存進 D1 資料庫 → 立刻觸發 GitHub 上的 bot 工作流程。
// bot 執行時用 GET /updates 取走訊息、處理完用 POST /ack 刪掉 —— 和 Telegram getUpdates 的用法一樣,
// 所以觸發失敗也不會掉訊息:定時的那次執行一樣會來取。
//
// 需要的設定(Cloudflare 後台 → Worker → Settings):
//   Bindings  → D1 database,變數名稱 DB
//   Variables → 三個 Secret:TG_WEBHOOK_SECRET、QUEUE_SECRET、GH_TOKEN
//   (GH_TOKEN 只需要 inat-victoria-bot 這個 repo 的 Actions 讀寫權限)
// 這個檔案不含任何密鑰或個人資料;訊息內容只存在 D1,處理完就刪除,不寫進記錄。

const REPO = "TommyPai/inat-victoria-bot";
const WORKFLOW = "bot.yml";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    await env.DB.prepare("CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, body TEXT NOT NULL)").run();

    // Telegram → 這裡:用 setWebhook 時設定的 secret_token 驗證來源
    if (request.method === "POST" && url.pathname === "/telegram") {
      if (!same(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), env.TG_WEBHOOK_SECRET)) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json();
      if (typeof update.update_id !== "number") return new Response("ok");
      // 寫入失敗會回 500,Telegram 會自動重送
      await env.DB.prepare("INSERT OR IGNORE INTO updates (id, body) VALUES (?, ?)")
        .bind(update.update_id, JSON.stringify(update)).run();
      ctx.waitUntil(dispatch(env));
      return new Response("ok");
    }

    // bot → 這裡:用 QUEUE_SECRET 驗證
    if (!same(request.headers.get("Authorization"), `Bearer ${env.QUEUE_SECRET}`)) {
      return new Response("forbidden", { status: 403 });
    }
    if (request.method === "GET" && url.pathname === "/updates") {
      const { results } = await env.DB.prepare("SELECT body FROM updates ORDER BY id LIMIT 100").all();
      return Response.json({ ok: true, result: results.map((r) => JSON.parse(r.body)) });
    }
    if (request.method === "POST" && url.pathname === "/ack") {
      const { offset } = await request.json();
      await env.DB.prepare("DELETE FROM updates WHERE id < ?").bind(Number(offset)).run();
      return Response.json({ ok: true });
    }
    return new Response("not found", { status: 404 });
  },
};

// 觸發 GitHub 上的 bot 執行一次(同時有多則訊息時,GitHub 會排隊,每次執行都會把佇列取完)
async function dispatch(env) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "inat-telegram-webhook",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (r.status !== 204) console.log(`dispatch failed: ${r.status}`); // 只記狀態碼,不記內容
}

// 長度不同或內容不同都回 false;逐字比較到最後,不提早結束
function same(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
