# Planning meta — cross-agent reminders

> 本檔案不是 agent 角色，是 **plan phase 共用的方法論 reminder**。
> 任何 agent 進入 plan phase 前都應該掃一次。
> 各 agent 自己的 CLAUDE.md 在 `planning/<agent>/CLAUDE.md`。

---

## 1. Operator persona pass（必跑）

Plan 任何 surface 時，使用者 / API consumer / agent client 是 first-class persona，但**還有一個 persona 通常被遺漏：operator**。

Operator 是「未來會回來改設定、debug、recover 這條 surface 的人」——本 phase 1 通常就是你自己，但你在做 setup / 故障排除時的視角，跟你做 product feature 時不一樣。

Plan 鎖定前必須對每個 surface 答這三條：

1. **Admin / config 路徑**：這條 surface 要改設定（OAuth client、scope、quota、flag 等）時走哪個入口？UI / CLI / API / 手 SQL 都要列。`「進 admin UI」` 不夠具體——admin UI URL 是什麼、要什麼權限。
2. **Break-glass / recovery 路徑**：主路徑（外部 IdP / 第三方 API / managed service）失效時怎麼救？例：Google IdP 整條停 → 怎麼登入；Stripe webhook 卡住 → 怎麼手動補單；DB migration 中段 → 怎麼回滾。「等對方修」不算答案。
3. **跟主使用者流程的拓樸關係**：operator 入口跟 user 入口是同一頁、分頁、還是隱藏路徑？分得太開→ operator 找不到；混在一起→ user 困惑。

**這條 pass 跟「security review」/「failure-mode review」不同**：security 看「壞人能做什麼」，failure-mode 看「系統怎麼掛」，operator pass 看「人在系統還活著時怎麼介入」。

### 為什麼存在這條 reminder

retrospective from T-068：`planning/frontend/oauth-integration.md` §1.1 鎖「Sign in with Google 單一按鈕」，反方案 dual button 寫了「multi-user 才有意義」拒絕。理由內部一致，但 plan time 把 user 跟 operator 折成同一人——實際 setup / debug 階段 reveal：

- Google OAuth client Testing mode refresh 7d 過期 → 沒帳密 fallback 無法 recover
- Setup / 改 Authentik 設定要 akadmin 進 admin UI → 「`/oauth/if/admin/`」這條 URL 在 UI 上完全沒提示，要記
- 一旦 OAuth-only cutover 完成，連舊 JWT 登入頁也沒了，break-glass 完全消失

T-068 是把這條補上，但更值得記的是「plan phase 漏了 operator persona」這個 pattern，未來 plan 任何新 surface 都套這條 pass。

---

## 2. 觸發時機

- **新 surface plan phase**（M3.5b、M3.5c、M4 之後每個 milestone 開 plan）：必跑這條 pass，把答案寫進該 surface 的 planning doc 對應節
- **既有 plan 改寫**：若是 retrospective 出來才補的，順手在 doc 留 `> 2026-XX-XX: operator pass retrofit — see T-XXX` 標記
- **不必跑的情境**：純內部 refactor、ticket 範圍純 backend 邏輯不涉及 surface 配置時

---

## 3. 漏算了怎麼補

不必開單。發現某個既有 surface 漏了 operator path：

1. 開 `T-XXX-…-operator-amendment.md`（type `docs` 或 `chore`），sprint 掛在「Post-Mx UX follow-ups」或對應的 backlog
2. ticket Scope 同時列：補上 operator path + 修對應 planning doc（讓後人讀的是新版）
3. 不要回頭去翻舊 ticket DONE 的 commit message——`planning/*/` 才是 spec source of truth

T-068 是這個 pattern 的第一張範例。
