# T-075: T-073 regression — SPA wraps the cf-google-init URL in the wrong encoding layer, `next` never reaches the executor

**Status:** DONE
**Sprint:** Backlog（post-3.5a；T-073 regression fix — T-073 AC#4 CDP 測試抓到）
**Est:** XS
**Depends on:** none（T-073 已 merge）
**Related:** T-073（本單修它 ship 的 bug）、T-076（同 CDP 驗證 reveal 的下一道牆 — flow interface CORS）

---

## Scope

修 T-073 `buildSourceInitUrl` 把 `next` 包錯一層的 bug：SPA 產的是 `/oauth/if/flow/cf-google-init/?query=next=<authorize URL>`，但 Authentik flow **interface** 會把整個 `window.location.search` 自己 bundle 進 executor API 的 `?query=` —— 結果變成 `query={query: "next=..."}`，沒有 `next` key，`_prepare_flow` 還是 fallback 到 `/if/user/`。T-073 沒真的修好。

### 怎麼發現的（2026-05-14，T-073 AC#4 CDP 測試）

T-073 merge（PR #99）後跑 AC#4：CDP 連本機真實 Chrome、清掉 SPA storage + `authentik_session` cookie、`:5173/login` → 點「使用 Google 登入」。結果落在 `http://localhost/oauth/if/user/`、畫面 "Permission denied — Interface can only be accessed by internal users"（operator 是 enrollment 建的 `external` user，被 `/if/user/` 擋）。即 `_prepare_flow` 拿到空的 `SESSION_KEY_GET`、fallback 到 `/if/user/` —— T-073 要修的 wall 3 還在。

### Root cause

兩層 API 對 `next` 的傳遞慣例不同，T-073 搞混了：

- **Flow executor API**（`/api/v3/flows/executor/<slug>/`）讀 `?query=<urlencoded querystring>`（`executor.py:159` `QueryDict(request.GET.get("query",""))`）。
- **Flow interface**（`/if/flow/<slug>/`）吃**普通 query params**（`?next=X`），由前端自己 bundle：`FlowInterface-2024.12.5.js` 的 `flowsExecutorGet({ flowSlug, query: window.location.search.substring(1) })` —— 它把整條 `location.search`（去掉 `?`）當成 `query` 丟給 executor API。

T-073 的 `buildSourceInitUrl` 產 `/if/flow/cf-google-init/?query=next=X`，interface 讀 `location.search`=`?query=next=X`、`.substring(1)`=`query=next=X`、丟給 executor → `QueryDict("query=next=X")` = `{query: "next=X"}` → **沒有 `next` key** → `_prepare_flow` `.get("next", "authentik_core:if-user")` → `/if/user/`。

（T-073 的 curl 驗證會過，是因為它直接打 executor API —— 那層確實要 `?query=`。SPA 打的是 interface，慣例不同。）

**In scope:**
1. `buildSourceInitUrl` 改產 `/oauth/if/flow/cf-google-init/?next=<encodeURIComponent(authorizeUrl)>` —— 普通 `next` param、單層編碼。interface 前端會自己 bundle 成 executor 的 `?query=`。
2. 修對應的 docstring（現在解釋的是錯的雙層 bundle）。
3. 修 unit tests（`oauth-client.test.ts`、`login.test.tsx`）+ e2e spec（`oauth-login.spec.ts`）—— 它們斷言的是錯的 URL 結構。
4. 修 `planning/devops/authentik-stack.md` §5.2.1 —— SPA URL shape 寫錯、curl 驗證步驟要區分 interface vs executor API 兩條路徑。

**Not in scope**（保留給其他單）：
- `cf-google-init.yaml` blueprint 本身（flow / RedirectStage / binding 都對，不用改）。
- `require_unauthenticated` re-login 互動（T-073 已分析；本單只修 encoding，re-verify 時若 reveal 新問題另開）。
- T-074 的 open-redirect hardening。

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.2.1 — T-073 落地的機制說明，本單要修 SPA URL shape + 驗證步驟
- `tickets/DONE/T-073-*.md` §Resolution — T-073 的完整脈絡
- memory `reference_local_chrome_cdp_connection` — re-verify 用的 CDP 連線方式

---

## Acceptance criteria

- [x] `buildSourceInitUrl` 產 `/oauth/if/flow/cf-google-init/?next=<single-encoded authorize URL>`
- [x] CDP 驗證 encoding fix 正確 —— network log 確認 SPA 導到正確的 `/oauth/if/flow/cf-google-init/?next=...`、flow interface 也發出格式正確的 `flows/executor/cf-google-init/?query=next%3D%252Foauth...` 呼叫。**完整走到 Dashboard 被 wall 4（flow interface XHR CORS）擋住 → 拆 T-076**（本單 scope 只到 encoding 修對；end-to-end 是 T-073+T-075+T-076 合起來）
- [x] `oauth-client.test.ts` / `login.test.tsx` / `oauth-login.spec.ts` 更新成斷言新 URL 結構（含「不得有 `query` param」regression guard），unit 全綠
- [x] `authentik-stack.md` §5.2.1 SPA URL shape + 驗證步驟修正
- [ ] CI 全綠（PR #100 auto-loop 驗）

---

## Resolution（2026-05-15）

**Encoding fix 確認正確。** T-073 把 `next` 包成 `?query=next=`，但 flow interface 前端（`FlowInterface-2024.12.5.js`）會自己把 `window.location.search` bundle 進 executor 的 `?query=` —— 多包一層 → executor 拿到 `{query: "next=X"}` 沒有 `next` key。改成 plain `?next=` 後，CDP network log 確認 flow interface 發出的 executor 呼叫是正確的 `flows/executor/cf-google-init/?query=next%3D%252Foauth%252Fapplication...`。

**但 CDP 驗證同時 reveal 了下一道牆（wall 4）：** flow interface 載得起來、executor 呼叫格式也對，但那些 XHR 打的是 Authentik 的絕對 `base_url`（`http://localhost/oauth/api/...`，無 `:5173`），跟 SPA 所在的 `http://localhost:5173` 跨來源 → 被 CORS preflight 擋掉 → interface 卡在 `Loading…`。這是 dev-`:5173`-only（prod/e2e 同源無此問題），是 T-073「走 flow interface」撞 T-070 dev-proxy topology 的後果。**拆 T-076 處理**，使用者 2026-05-15 拍板「先 ship T-075、再做 T-076」。

**踩過的坑（記給後人）：** 前幾輪 CDP 測試其實是打到 **stale 的 pre-T-073 SPA code** —— dev `web` 容器的 Vite dev server 因 Windows→Docker bind-mount file-watcher 沒抓到變更，一直 serve 舊版（`curl :5173/src/lib/oauth-client.ts` 看得到舊 code）。`docker compose restart web` 讓 Vite cold-start 重掃 source 才 serve 到正確版本（cold-start 要 >60s）。OAuth/login 改動用 CDP 驗證前，先確認 Vite 真的 serve 到當前 code。

---

## Files expected to touch

- `web/src/lib/oauth-client.ts` (edit — `buildSourceInitUrl` + docstring)
- `web/src/lib/oauth-client.test.ts` (edit)
- `web/src/routes/login.test.tsx` (edit)
- `web/tests/e2e/oauth-login.spec.ts` (edit)
- `planning/devops/authentik-stack.md` §5.2.1 (edit)
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 已新增/更新對應 spec —— `oauth-login.spec.ts` 的 Google-entry test 改成斷言新的 `?next=` URL 結構。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`（SPA URL builder + 文件；不碰 backend endpoint）

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### 為什麼 T-073 的 CI / 驗證沒抓到

- T-073 的 curl 驗證打的是 **executor API**（要 `?query=`），不是 SPA 實際打的 **interface**（要普通 params）—— 兩層慣例不同，curl 過了不代表 SPA path 對。
- e2e（`oauth-login.spec.ts`）assertion (a) 斷言的是 T-073 自己建的（錯的）`?query=next=` 結構，等於把錯誤假設寫進測試 → 測試過了也沒意義。assertion (b) 只驗 RedirectStage 有 forward，跟 `next` 有沒有傳成功無關。
- AC#4 是真人 Google round-trip，CI 結構上做不到（e2e 沒種 Google source）—— 只能靠 merge 後 CDP 手動驗。memory `feedback_verify_oauth_flow_via_cdp_before_ship` 講的就是這個：OAuth/login 改動 ship 前該先 CDP 走 end-to-end。T-073 是 merge 後才驗到，下次該 ship 前驗。
