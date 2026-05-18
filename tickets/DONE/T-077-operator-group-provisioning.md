# T-077: §5.7 operator-provisioning runbook never adds the operator to `cf-agent-default` — authorize endpoint denies them

**Status:** TODO
**Sprint:** Backlog（post-3.5a；operator-provisioning gap — T-076 CDP 驗證 reveal，wall 5）
**Est:** S
**Depends on:** none
**Related:** T-069（§5.7 operator provisioning runbook —— 本單補它的缺口）、T-071（backend `User` row auto-provisioning —— 同一個「新 operator 進不來」問題的另一半）、T-076（同 CDP 驗證 reveal）

---

## Scope

修「真人 operator 過了 Authentik 登入、卻在 `/oauth/application/o/authorize/` 被 'Permission denied' 擋掉」這道牆（wall 5）：`Character Foundry SPA` application 有一條 PolicyBinding 綁 `cf-agent-default` group，但 `authentik-stack.md` §5.7 的 operator-provisioning runbook 從沒把 operator 加進那個 group。

### 怎麼發現的（2026-05-15，T-076 CDP 驗證）

T-076 修好 flow-interface CORS 後，CDP 跑 fresh-session Google 登入 —— flow 一路通到 `/oauth/application/o/authorize/`，但回 "Permission denied — Request has been denied"。查 Authentik：`Character Foundry SPA` application `policy_engine_mode=any` + 一條 PolicyBinding（order 0）綁 `cf-agent-default`；operator `leoyeh906`（enrollment 建的 `external` user）`ak_groups` 是空的 → 不滿足任何 binding → 拒。手動 `g.users.add(u)` 把 operator 加進 `cf-agent-default` 後，CDP 重跑就一路走到 Dashboard。

### Root cause

- `authentik-stack.md` §5.5 有寫「把 Workspace 內每個會用 agent 的 user 加進 `cf-agent-default`」—— 但 §5.5 是講 **agent client** 的 group/policy 設定。
- §5.7「Provision a dev operator」只涵蓋 §5.7.1（Authentik user，enrollment flow 自動建）+ §5.7.2（backend `User` row，`provision-operator` CLI）—— **沒提 group membership**。
- enrollment flow 建的新 Authentik user 預設不在任何 group。所以照 §5.7 跑完的 operator，Authentik 認得、backend 也認得，但 **SPA application 的 group policy 不放行**。

**In scope:**
1. `authentik-stack.md` §5.7 補一節（§5.7.3 之類）：operator 也要進 `cf-agent-default`（或評估該不該有獨立的 operator group）。列具體指令（`ak shell` 或 admin UI 或 blueprint）。
2. 評估能不能把它做進 `provision-operator` CLI（CLI 目前只建 backend row；可順手呼 Authentik API / `ak shell` 把 Authentik user 加進 group）—— 或至少在 CLI 輸出提醒這一步。
3. §5.9 checklist 同步補一條。

**Not in scope:**
- backend `User` row auto-provisioning（T-071）。
- `_resolve_oauth` first-login auto-provisioning（T-071 / M3.5b）。
- 「authorize 被拒時的 UX」（目前是 Authentik 原生 "Permission denied" 頁，沒給 operator 任何指引）—— 若要改善 deny UX 另開單。

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.5（group/policy）、§5.7（operator provisioning runbook —— 本單補它）、§5.9（checklist）
- `infra/authentik/blueprints/cf-e2e-bootstrap.yaml` —— e2e 怎麼把 test user 綁進 `cf-agent-default`（`users` FK list，見 memory `authentik_blueprint_2024_12_gotchas`）
- `tickets/DONE/T-069-*.md` —— `provision-operator` CLI 現況

---

## Acceptance criteria

- [ ] 照 §5.7 更新後的 runbook 跑完，一個全新 operator 能登入走到 SPA Dashboard，不被 authorize endpoint 擋
- [ ] §5.7 + §5.9 已更新，明確涵蓋 group membership 這一步
- [ ] （若做進 CLI）`provision-operator` 把 operator 加進 `cf-agent-default`，或至少輸出提醒
- [ ] 既有 e2e 維持綠

---

## Files expected to touch

- `planning/devops/authentik-stack.md` §5.7 / §5.9 (edit)
- `api/app/cli.py`（若把 group-add 做進 `provision-operator` CLI — edit）
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 預期 **N/A** —— 文件 +（可能）CLI 改動，不碰 React Router route / SPA code。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

dev 環境目前已手動把 `leoyeh906@gmail.com` 加進 `cf-agent-default`（T-076 驗證時做的），所以 dev 當下可動；本單是把這步寫進 runbook / CLI，讓「下一個 operator」不用重新踩。
