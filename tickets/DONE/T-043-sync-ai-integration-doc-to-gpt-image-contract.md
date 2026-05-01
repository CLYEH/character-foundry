# T-043: sync planning/backend/ai-integration.md to real gpt-image API contract

**Status:** SUPERSEDED by T-048
**Sprint:** 3
**Est:** XS
**Depends on:** T-042（修了 code，但 planning 還沒同步）
**Related:** T-044（contract test）、T-048（吸收本張 scope）

> **2026-05-01 supersede 註：** 本張 ticket 的 scope（同步 `ai-integration.md` §3 範例 body）已被 T-048 完全吸收——T-048 一次同步 T-042 / T-045 / T-046 / T-047 全部今日 contract drift。本張不單獨實作，留檔做 audit trail。

---

## Scope

T-042 把 `api/app/ai/gpt_image_2.py` 改齊了 OpenAI `gpt-image-*` API 真實 contract，但 `planning/backend/ai-integration.md` §3 的範例 request body 還是舊 dall-e-3 shape（含 `seed` / `quality=hd` / `response_format` / 重複 `image` 欄名）。任何人未來 copy-paste 範例又會中同一個 bug。

**In scope:**
- `planning/backend/ai-integration.md` §3 範例 body 更新：
  - text2image / image2image / inpaint：移除 `seed` / `quality=hd` / `response_format`
  - edit_image2image multi-image：欄名從重複 `image` 改成 `image[]`
- 加一段 "gpt-image vs dall-e-2/3 contract 差異" 註解，說明哪些 param 是 dall-e-only、為什麼不要塞
- 引用 T-042 的 empirical evidence 連結（commit SHA 或 PR #53）作為 ground truth 來源

**Not in scope:**
- 任何 code 改動（T-042 已經改完）
- Veo 部分（`ai-integration.md` §4）的 example body sync
- 其他 planning doc 的同類掃過——要的話另開

---

## Planning refs

- `planning/backend/ai-integration.md` §3 — 待改的檔案
- `tickets/DONE/T-042-fix-gpt-image-api-contract.md` — empirical evidence + 修法決策
- PR #53（merged 2026-05-01）— commit history 完整 evidence

---

## Acceptance criteria

- [ ] `planning/backend/ai-integration.md` §3 範例 body 不再含 `response_format` / `seed` / `quality=hd`
- [ ] §3 multi-image edits 範例使用 `image[]` 欄名
- [ ] §3 加上 "gpt-image vs dall-e contract 差異" 註解
- [ ] `grep -E "response_format|quality.*hd|seed" planning/backend/ai-integration.md` 只剩用來說明「不要塞這些」的反面教材

---

## Files expected to touch

- `planning/backend/ai-integration.md` (edit)

---

## Notes

- 這張單純文件，不過 CI / unit test 路徑——但走標準 docs PR flow 就行
- 為什麼分出來不在 T-042 一起做：T-042 已經是 surgical bug fix scope，再 fold planning sync 會稀釋 review 焦點
