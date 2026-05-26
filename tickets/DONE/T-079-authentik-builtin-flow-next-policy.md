# T-079: Extend `next` validation policy to Authentik built-in flows

**Status:** TODO
**Sprint:** Backlog（post-3.5a；security hardening — T-074 follow-up）
**Est:** S
**Depends on:** T-074（policy 物件、binding shape、_deny session sanitization 都已落地，本單只是把 binding 多綁幾條）
**Related:** T-074（同漏洞同 policy，scope 拆分）

---

## Scope

把 T-074 已經寫好的 `cf-google-init-next-validation` 同一條 expression policy，binding 到 SPA 能直接 navigate 到的 Authentik 內建 flow（`default-authentication-flow` / `default-source-authentication` / `default-source-enrollment`），closing T-074 「Not in scope」段註明的同一個 open-redirect class 在 Authentik 內建 flow 上的曝面。

### 背景（T-074 ship 時 deferred）

T-074 ship 出來時 close 了 `/oauth/if/flow/cf-google-init/?next=evil.com` 這條 SPA 主要攻擊鏈，但同一個漏洞 spec **存在於每一條 Authentik flow-executor URL**：

```
/oauth/if/flow/default-authentication-flow/?next=https://evil.com
/oauth/if/flow/default-source-authentication/?next=//evil.com
/oauth/if/flow/default-source-enrollment/?next=/\evil.com
```

任何 attacker 手 craft 一條 phishing link 指過去、得 victim 走完登入，Authentik core `_flow_done()` 的 `PLAN_CONTEXT_REDIRECT` path（不驗 host）就會把 victim bounce 到 evil.com。Phishing 入口比 `cf-google-init` 不顯眼但**完全可達**。

T-074 security review 判定「正確地 defer 出 T-074 scope」，但屬於 deployment 內 standing risk —— **不該只留在 planning doc 註腳**，要有獨立 tracked ticket。本單就是那條。

### In scope

- 把 `cf-google-init-next-validation` policy（T-074 已建好）binding 到：
  - `default-authentication-flow`（SPA 帳密 fallback 主要 entry）
  - `default-source-authentication`（OAuth source matched-user 走的 flow）
  - `default-source-enrollment`（OAuth source new-user enroll 走的 flow）
- Binding shape 完全 mirror T-074 cf-google-init：兩條 PolicyBinding（target=flow + target=FlowStageBinding，後者 `re_evaluate_policies=true` + `evaluate_on_plan=true`），讓 race protection + session sanitization 都套用
- 在現有 blueprint 裡 codify（不新增 blueprint 檔，傾向加進 `cf-e2e-bootstrap.yaml` 或新開 `cf-builtin-flow-hardening.yaml`，視 mount 範圍而定 —— 評估時決定）
- 驗 binding 對 SPA 既有 happy path（password fallback + Google login）不誤殺
- 驗 attacker 真打這三條 flow URL 的 `?next=evil.com` 都被擋

### Not in scope（保留給其他單）

- 改 Authentik core 程式碼（不可行，container image）
- T-074 ship 出的 `cf-google-init` binding（已 done）
- 新增別的 flow（multi-IdP、admin 後台、其他 SPA）—— 等該 surface 真做時再 retrofit

### 需要評估（plan phase 第一件事）

1. **Built-in flow binding 的 blast radius：** 把 same-origin policy 推到 `default-authentication-flow` 會 affect 所有 SPA、Authentik admin UI、未來任何 application 的 login。需要列出所有當前 + future 預期 use case，確認 same-host 假設成立。例：admin UI 是同一個 host（同 nginx）→ OK；M3.5b MCP server 若 OAuth flow 走別 host → 需要例外。
2. **多 source 場景：** 目前只一條 Google OAuth Source；未來加 GitHub OAuth source（候選）的話 `default-source-authentication` / `default-source-enrollment` 會被新 source 共用，binding 是否需要 source-specific 處理？傾向「不需要，policy 只看 `next` URL 對 host 的 same-origin 關係，跟 source 無關」，但要明確 evaluate。
3. **`default-source-pre-authentication` 是否也要綁：** Authentik flow 列表還有這條，evaluate 是否在 SPA 攻擊鏈上、是否要 retrofit。

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.2.1b — T-074 落地的 policy 物件 / dual-binding shape / `_deny()` session sanitization / get_host trust invariants（**本單直接重用，不重寫**）
- `tickets/DONE/T-074-authentik-flow-next-validation-policy.md` — 本單 deferred 出處
- `infra/authentik/blueprints/cf-google-init.yaml` — T-074 落地的 reference shape
- `infra/authentik/blueprints/cf-e2e-bootstrap.yaml` — 看現有 `default-source-authentication` upsert（T-078）pattern，新 binding 可能加在這檔或新開 hardening blueprint

---

## Acceptance criteria

- [ ] `/oauth/if/flow/default-authentication-flow/?query=next=https://evil.com` → `ak-stage-access-denied`，不 redirect 到 evil
- [ ] `/oauth/if/flow/default-source-authentication/?query=next=//evil.com` → `ak-stage-access-denied`
- [ ] `/oauth/if/flow/default-source-enrollment/?query=next=/\evil.com` → `ak-stage-access-denied`（防 `_flow_done` fallback emit `\evil.com` 被瀏覽器當 `//evil.com`）
- [ ] SPA password fallback happy path（既有 `web/tests/e2e/oauth-login.spec.ts` test #1）維持綠
- [ ] SPA Google entry happy path（同 spec test #3）維持綠
- [ ] Race regression test：同一 session 連跑 legit `next` → evil `next` 三條內建 flow，evil 都被擋（包含 admin-logged-in session 變體 —— 內建 flow 比 `cf-google-init` 更常被 admin UI 走，race surface 更廣）
- [ ] Blueprint apply 乾淨（`BlueprintInstance.status == successful`）

---

## Files expected to touch

- `infra/authentik/blueprints/cf-google-init.yaml` 或新檔 `infra/authentik/blueprints/cf-builtin-flow-hardening.yaml`（plan phase 決定）—— 加 binding 到內建 flow
- `planning/devops/authentik-stack.md` §5.2.1b 末段「**為什麼只綁 `cf-google-init`**」段更新 / 刪除（T-079 上線後不再 only-cf-google-init）
- `STATUS.md`（edit）
- 可能：`docker-compose.test.yml` / `docker-compose.override.yml` 若新檔需 mount

> **E2E coverage gate（CONTRIBUTING §3.5）：** 預期 **N/A** —— 改的是 Authentik flow policy / blueprint，不是 React Router route / SPA code。既有 e2e 須維持綠。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`（Authentik flow policy；不碰 backend endpoint）

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### T-074 已落地、本單只是延伸

`cf-google-init-next-validation` expression policy 物件 + `_deny()` session sanitization + dual-binding shape（target=flow + target=FlowStageBinding with `re_evaluate_policies`）全部在 T-074 落地驗證過。本單純粹是把同一條 policy 多綁 3-4 條 flow。實作量極小，重點在 plan phase 把「built-in flow 綁這條會不會誤殺」想清楚。

### Built-in flow upsert pattern

`cf-e2e-bootstrap.yaml` 已有先例：T-078 把 `default-source-authentication.authentication` 從 `require_unauthenticated` 改成 `none`：

```yaml
- model: authentik_flows.flow
  identifiers:
    slug: default-source-authentication
  attrs:
    authentication: none
```

`identifiers: slug` 是 idempotent upsert。本單可 mirror 這個 pattern：對 built-in flow 加 `policies` attrs 或 PolicyBinding entries 指向它。

### 環境分布注意

- `cf-google-init.yaml` 在 dev（`override.yml` 單檔 mount）+ e2e（`test.yml` dir mount）都生效
- `cf-e2e-bootstrap.yaml` 只在 e2e（`test.yml` dir mount）生效；dev 跑 T-078 fix 要走 `ak shell` 手動或自己 codify
- 本單若新開 `cf-builtin-flow-hardening.yaml`，要明確 decide dev / e2e / prod 哪幾個環境 mount。Prod ship 同 §5.9 checklist 邏輯：列一條 admin-UI 動作或自己 codify 一條 prod-blueprint
