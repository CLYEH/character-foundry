# T-XXX: {短標題}

**Status:** TODO
**Sprint:** {N}
**Est:** {XS | S | M}
**Depends on:** {T-XXX, T-YYY | none}
**Related:** {T-XXX（之後會用到本單產出的其他單）}

---

## Scope

一句話描述這張單完成代表什麼。

**In scope:**
- …
- …

**Not in scope**（保留給其他單）：
- …

---

## Planning refs（開工前必讀）

- `planning/xxx/yyy.md` §N — 原因
- `planning/xxx/zzz.md` §M — 原因

---

## Acceptance criteria

- [ ] …
- [ ] …
- [ ] 測試都綠（列具體測試指令）

---

## Files expected to touch

- `path/to/new-or-edited-file` (new | edit)
- …

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

新增 / 改動 endpoint 時，列每個 endpoint 需要的 scope。沒新增 endpoint 寫 `n/a`。

| Endpoint | Scope |
|---|---|
| `POST /v1/...` | `character:write` |

決策出處：`planning/backend/oauth-mcp-integration.md`

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

這張 ticket 對 `app/mcp/tools/` registry 的影響。

- **新 tool**：列 `name` / `scopes` / `bundles`
- **修 tool**：列改了什麼
- **無影響**：寫 `n/a`

決策出處：`planning/backend/oauth-mcp-integration.md`

---

## Notes

任何實作 hint、已知陷阱、曾討論過的 trade-off。
