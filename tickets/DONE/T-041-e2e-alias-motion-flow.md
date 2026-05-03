# T-041: E2E — Alias creation + motion preset smoke

**Status:** TODO
**Sprint:** 3
**Est:** S (1h)
**Depends on:** T-031, T-032, T-033, T-034, T-036, T-037, T-038（M3 milestone gate）
**Related:** T-026（既有 Sprint 2 E2E smoke 可參考結構）

---

## Scope

Playwright smoke test：跑完 「登入 → 建 character → 確立 base → 加 alias（純 text）→ 生成 base 的 preset_wave motion」整段。Backend AI 跑 stub 模式（T-014 + T-029 + T-030 stub），結果可預期。

**In scope:**
- 在既有 E2E setup（T-012 / T-026 fixture）上加 `e2e/alias-motion-smoke.spec.ts`
- 步驟：
  1. login（reuse fixture）
  2. 建 character（template mode，最少必填）
  3. 走 creation session：跑一次 checkpoint → 選作 base
  4. 進 character detail，點 `[+ 新增 Alias]` → alias edit page
  5. 填 alias 名稱 + freeform_note → submit → 等 SSE 完成 → 自動回 detail
  6. 看到 alias 出現在列表
  7. 點 base 的 preset_wave empty cell → 等完成 → 看到縮圖
  8. 點縮圖 → motion lightbox 顯示 video element
- 全程 stub backend，等待時間應 < 30s
- Assertions 用 data-testid（既有 convention）
- CI workflow（T-004 / T-026 已建）—— 確認 E2E job 跑得到本 spec

**Not in scope:**
- Custom motion E2E（Sprint 5 polish 再加）
- Inpaint E2E（Phase 1 stub 不模擬 mask 路徑差異，本單跳過）
- Veo i2v 影片實際播放驗證（只驗 `<video>` element 存在 + src 非空）
- Reference image upload E2E（同上理由）

---

## Planning refs

- `planning/devops/ci-cd.md` — E2E job 配置
- T-026 spec 為結構對照
- 各 Sprint 3 ticket 的 acceptance（本單把它們串起來）

---

## Acceptance criteria

- [ ] `pnpm -C web e2e:run -- alias-motion-smoke` 本機跑通
- [ ] CI 上 E2E job 涵蓋本 spec 並全綠
- [ ] Spec 整段 timeout 設 60s 仍能在 stub backend 下穩定通過（< 30s 平均）
- [ ] 失敗時 screenshot / trace 自動上傳（既有 fixture）

---

## Files expected to touch

- `e2e/alias-motion-smoke.spec.ts` (new)
- `e2e/fixtures/`（若需）—— 加 alias / motion fixture helpers
- `.github/workflows/`（若 E2E job 名單需更新）—— 多數情況 glob `e2e/*.spec.ts` 就涵蓋

---

## Notes

- Backend stub 模式：env `AI_IMAGE_BACKEND=stub`、`AI_VIDEO_BACKEND=stub`（T-014 / T-029 提供）
- Stub 回傳的圖 / 影片 fixture 都要小（<1MB），E2E run 才不會卡
- 本單通過 = M3 milestone（Aliases + Motions working）達成；merge 同時更新 STATUS.md 把 M3 勾掉
- 若任何前置 ticket 未 merge，本單依 PR 把該 ticket 列為 blocker（draft PR）；Sprint 3 結束時必須 active 並通過
