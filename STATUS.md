# Character Foundry — Implementation Status

> **Last updated:** 2026-05-13 — T-053 implemented: Authentik Google upstream IdP + 5 OAuth client registration runbook + `app/auth/mcp_clients.py` allowlist (Sprint 3.5a 第二張). 程式碼面：`api/app/auth/mcp_clients.py` 新增，declarative-only — `CANONICAL_SCOPES` frozenset 鎖 Phase 1 唯一 5 條 scope 字串（`character:read` / `character:write` / `task:read` / `task:cancel` / `usage:read`）、`M2M_DEFAULT_SCOPES = ["character:write", "task:read"]`（Q5 sub-5a narrow default）、`ALLOWED_CLIENTS` dict 4 個 entries（`claude-code` / `vs-code` / `cursor` 走 `scopes=None` delegated；`cf-test-agent` 顯式 override 拿全 5 scope）。SPA `character-foundry-spa` 刻意**不**進 allowlist：allowlist 是 MCP server 邊界（`/mcp/*` access control），SPA 走 `/v1/*` 不需要過這層；module 名 `mcp_clients` 已表達這個語意，且 ALLOWED_CLIENTS 下方加 inline comment 鎖住這條決定（避免下個 reader / T-054 author 誤把 SPA 加進來）。Enforcement 一律 defer 給 T-054 middleware — 本單只放 declarative data 確保 T-053 → T-054 handoff 是 pure schema 移植不必動 OAuth 設定。`api/tests/arch/test_mcp_clients_allowlist.py` 加 7 條 static integrity test（canonical scope set 與 `tests.arch.test_layering.OAUTH_SCOPE_LITERALS` cross-pin 對齊 — 不另開第三個 source of truth，drift 由「兩個 pin 必同步」鎖住，T-054 上線 `scopes.py` 後此 cross-pin TODO-marked 可以拆掉 / `M2M_DEFAULT_SCOPES` 全 canonical / M2M default **必為** `CANONICAL_SCOPES` 真子集 (`<` 不是 `<=`，避免 narrow default 被誤拓成 full set) / 4 個 client membership 鎖定 / 每個 client `scopes` 列表全 canonical / `cf-test-agent` 必拿全 5 scope (CI smoke coverage gate) / 3 個 delegated client 必為 `scopes=None`），放在 `tests/arch/` 不放 `tests/auth/` 是因為 `tests/auth/conftest.py` 有 `_migrate_once` autouse fixture 走 DB migration，pure static data 測試在 `tests/arch/` (T-059 import-linter 旁邊) 更乾淨。Runbook 面：`planning/devops/authentik-stack.md` 新增 §5 Initial setup（8 sub-section）— §5.1 admin 首登走 `docker compose logs authentik-{server,worker} | grep recovery` 拿 recovery URL（不放 `AUTHENTIK_BOOTSTRAP_*` env，per T-052 §Notes 該 race window 已用 loopback bind 砍掉）；§5.2 Google OAuth Source（slug `google` → callback `/source/oauth/callback/google/`、user matching mode `Link to a user with identical email address` 避免 Workspace 帳號分裂）；§5.3 5 條 Scope Mapping 逐條建立 + warning「拼錯一字 T-054 middleware 必爛」+ 列出每條對應的 endpoint family；§5.4 5 個 Provider + Application 對照表（SPA + 3 delegated agent 走 Public client + PKCE / `cf-test-agent` 走 Confidential client + client_credentials；access token TTL 1h per Q5 sub-5b、refresh policy 區分 SPA 30d / delegated 1h 無 refresh / M2M 不發 refresh）+ warning「client_id 拼錯 T-054 必爛」+ warning「`cf-test-agent` secret 不進 `.env.example` 只進 1Password」；§5.5 Group + Policy 綁定（`cf-agent-default` 兜 delegated 4 個 / `cf-test-agent-full` 給 M2M 拿全 scope）+ 「Authentik 文件對 M2M scope-group 互動講得不直觀」的迷路檢查清單；§5.6 verification 三段 — Google login (AC#1) / `curl client_credentials` (AC#7，含完整 curl + 預期 JSON shape + token claim 檢查方式) / access token 是 JWT 不是 opaque (T-054 走 JWT verify 非 introspection 的前提)；§5.7 backup（Phase 1 不寫 automated，M3.5 ship 前進 `operations.md`，含 `authentik_certs` named volume 一併備份的提醒——否則 OIDC signing key 倒掉 = 所有 token 失效）；§5.8 setup checklist 8 條對齊 AC。配套：`.env.example` 加 `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` 區塊（commented runbook hint + warning「這兩條是操作者 secret stash 不是 docker-compose runtime config — Authentik 從 admin UI 讀進自己的 postgres，不從 env 讀」，避免 reviewer 誤以為 docker-compose 該注入）；`planning/devops/environment-variables.md` 新增 §2.9 Google upstream IdP 表 + 同樣的 "operator stash not runtime config" 解釋；`planning/auth/open-questions.md` 在 Step 2 完成段下加「Q3 canonical scope 字串（T-053 lock）」表，明確列出 5 條的 canonical 字串、HTTP verb / endpoint family 對應、3 個權威來源（mcp_clients.py / Authentik UI / T-054 後的 scopes.py）、修改流程（不在 T-053 / T-054 scope，要開新 ticket 同步全部地方）— per ticket §Notes「建議在 Q3 補上 canonical 字串清單」。Acceptance 覆蓋：AC#5 (`mcp_clients.py` 建立內容對齊) 完全程式碼可驗 — 8 條 static test 跑過；AC#6 (`§5` setup 步驟完整可重現) runbook 落地；AC#1 / #2 / #3 / #4 / #7 需要 user 在 admin UI 跑 §5 → §5.8 checklist 才能勾完（純配置動作，本單交付 runbook 與 code module，PR body 標 Manual verification 區塊）。Gitleaks fix：runbook §5.6.2 原本 sample JSON 用一個 JWT-形狀的 truncated header 字串（base64url 開頭 + 省略號）被 generic-api-key 抓 false positive（entropy 3.94），改成描述性字串 `"<RS256 JWT — 3 base64URL segments separated by '.'>"`。Pre-push 兩個 reviewer round（engineering-code-reviewer + security-engineer per T-062 SEC chain trigger）的收斂結果一併折進初版 commit：(a) `environment-variables.md` 兩個 `### 2.9` heading 衝突 — Frontend 改 §2.10；(b) `.env.example` 在 GOOGLE_OAUTH_* 區塊下加「DO NOT add CF_TEST_AGENT_CLIENT_SECRET here」negative anchor — 避免 .env.example 模式 prime 後續 operator 把 5-scope M2M secret 順手 commit 進去；(c) `authentik-stack.md` §5.2 加 Workspace **`hd=` hosted-domain 限制**——「Link to a user with identical email address」相依 upstream IdP email 已驗 (Workspace tenant 是) 但 future operator 加第二條 OAuth Source (consumer Google / GitHub) 就會撞 classic account-takeover-by-email-claim；先把 `hd=<workspace-domain>` 鎖在 Source level，把這條 trust assumption anchor 在文件上；(d) §5.4 delegated agent 行（claude-code / vs-code / cursor）原寫 `Refresh token: 1h (per 5b — 過期重 consent)` 是錯的：Authentik 無「禁止發 refresh」開關，唯一機制是 provider scope list **不含 `offline_access`**。原寫 1h refresh TTL 反而比「不發」嚴格更糟 (token 仍可在 TTL 內 replay)。改為「`offline_access` 拿掉 = Authentik 不發 refresh」明確記錄，並把 `offline_access` 從 §5.4 第 1 步 Scopes 行 strip 出來只對 SPA 加；(e) §5.4 `cf-test-agent` 行加 **Grant types 鎖死只允許 `client_credentials`**——未來把 client type 翻 Public 或加 Auth Code 來「debug」會把 explicit-consent flow reach 到無人值守的 M2M client，blast radius 直接擴成 full delegated；把 grant type 邊界做進結構而非 behavioural；(f) §5.6.2 verification curl 用 `export CF_TEST_AGENT_CLIENT_SECRET=...` + `--data-urlencode` + `unset` envelope 取代 literal `<from-1Password>` 字面值，避免 operator paste 後 secret 留在 `~/.bash_history` / PowerShell `ConsoleHost_history.txt`；(g) `mcp_clients.py` ALLOWED_CLIENTS 下方加 SPA 排除註記、M2M_DEFAULT_SCOPES 註解明寫「dormant policy，T-054 wire」、`is_allowed_client()` helper 移除（YAGNI — `client_id in ALLOWED_CLIENTS` 直接用；T-054 若需要 widened API surface (e.g. `get_client_policy() -> ClientPolicy | None`) 那是不同 signature 不該預先 lock-in bool shape）；(h) test 端 `EXPECTED_SCOPES` literal 改成 `from tests.arch.test_layering import OAUTH_SCOPE_LITERALS` cross-pin（避免在 T-054 之前同時維護三份相同的 5-string list）+ TODO(T-054) anchor 對應將來 scopes.py 落地後該 cross-pin 拆除。**全套 ruff lint + format / mypy strict / bandit -ll / semgrep OWASP / 全 arch 測試（9 passed + 1 skipped intentional）一次過。** **不**做：Backend token middleware（T-054）、Frontend Sign in with Google 按鈕（T-056）、`app/auth/scopes.py` 集中化（T-054；當前 `mcp_clients.py` 是 sole canonical source；`tests/arch/test_layering.py::test_oauth_scope_source_is_centralized` 跳過 sentinel 留給 T-054）、Authentik blueprint-based config（admin UI 手動足以；blueprint M3.5 ship 後評估）、prod 環境 secret rotation 流程（operations.md 工作；M3.5 ship 前）。Previous: T-051 implemented: Veo 3.1 RAI filter detection + post-submit retry budget + audit on misleading `model_invalid_request` "returned 4xx" template. `api/app/ai/errors.py` 加新 factory `model_content_filtered`（`code=MODEL_CONTENT_FILTERED`、`retryable=True`、`status_code=502`、user-facing 訊息「目前無法生成此動作影片，系統正在自動重試」）並把 `model_invalid_request` 的 problem 模板拆兩條路徑：`http_status=<int>` → `"returned HTTP {status} for the request payload."`、`http_status=None`（detail-only path）→ `"returned an unexpected response."`。`map_response_to_agent_error` 4xx branch 改成 `model_invalid_request(model, detail=detail, http_status=status)`，所以 audit 範圍內 7 個 detail-only 路徑（Veo `submit response missing operation name` / `poll response was not a JSON object` / `operation.response did not include any video items` / `video item missing both bytesBase64Encoded and uri` / `videoUri redirect missing Location header` / `videoUri redirect chain exceeded` / `_map_operation_error INVALID_ARGUMENT`）通通不再硬塞「returned 4xx」誤導 on-call。`api/app/ai/veo_3_1.py` `_poll_until_done` 在 `done: true` 那刻並列 `payload.error` 檢查再加一條 `_extract_rai_filter_signal`，偵測 `raiMediaFilteredCount > 0` 或 `raiMediaFilteredReasons` 非空（同時 cover `response.*` 直接放 + `response.generateVideoResponse.*` nested 兩種 shape，後者是 user 真機 task `371fc9a8` 命中的），命中即丟 `MODEL_CONTENT_FILTERED` 並把 RAI reasons 串進 `detail`。`generate_i2v` 加一條外層 RAI retry 迴圈（budget = `VEO_RAI_MAX_RETRIES` + 1，default 2 → 共 3 次），每次 retry 走完整 `_submit_with_retry`→`_poll_until_done`→`_fetch_video_bytes`（重 submit 才有意義，per ticket §Notes），budget 耗盡才把 `MODEL_CONTENT_FILTERED` surface。RAI 失敗**不**回饋 breaker（operation 完成 = Veo 健康；連續 RAI false positive 不該推 unrelated caller 進 `MODEL_UNAVAILABLE`），靠 `_logger.warning("veo_rai_filter_retry", extra=...)` 每次 attempt 寫一筆結構化 log（含 attempt 編號、max_attempts、operation_name、RAI 原文 detail），budget 耗盡再寫 `_logger.error("veo_rai_filter_retry_exhausted", extra=...)`——ops 可以 grep 出 false-positive rate 才能調 `VEO_RAI_MAX_RETRIES`（沒這條會 blind-tune）。`status_code=502` 取捨：跟 `model_invalid_request` / `model_unavailable` / `model_quota_exceeded` 同 provider-issue 慣例；422 也合理（拿到 response 但結果不可用），但選 502 一致性比較重要——這個取捨在 PR body 寫清楚。`api/app/ai/config.py` 加 `veo_rai_max_retries()` 走 `_int_env("VEO_RAI_MAX_RETRIES", default=2, min_value=0)`，allow 0 讓 incident 時 ops 能立刻停血。Tests：`tests/ai/test_veo_3_1.py` 加 6 條 RAI / template 相關 case（user-reported shape 偵測、`response.*` 直接放也偵測、RAI 不 feed breaker、retry-within-budget 成功（assert 2 個 submit + 0 breaker failure）、budget 耗盡 surface `MODEL_CONTENT_FILTERED`（assert 3 個 submit + 0 breaker failure）、empty-response 路徑 problem 不再說 4xx）；`tests/ai/test_errors.py` 加 3 條 factory-level case（real 4xx 路徑 problem 包含 "HTTP 400"、detail-only 路徑不包含 "returned 4xx" 且包含 "unexpected response"、`model_content_filtered` envelope 結構 + retryable=True + 502）。`planning/backend/ai-integration.md` §4.4 在「Submit-only retry」原則下加 **RAI filter 例外** 子節（偵測點、budget、不 feed breaker、log 行為、user 訊息策略），`planning/devops/environment-variables.md` §2.2 加 `VEO_RAI_MAX_RETRIES` 一列。**全套 ruff lint + format / mypy strict / 全 ai 測試（158 passed, 3 deselected）一次過。** Default 取 2 的依據：`googleapis/js-genai#1272` 顯示 ~90% RAI false positive 在 1-2 retries 內清掉；更高的數字會在真正違規 prompt 上燒錢。retryable=True 取捨：跟 `prompt_content_policy`（non-retryable，user 真的寫了違規 prompt 被 OpenAI 擋）刻意分開——Veo RAI 是上游 flaky，不該叫 user 改 prompt。**不**做：把 RAI reasons 字串回前端（planning §3.5 反 leak 原則）、改 i2v retry 整體哲學、gpt-image 路徑 RAI 對應檢查（沒看到類似回報）。Previous: T-052 implemented: Authentik OSS docker service stack 加入 docker-compose（Sprint 3.5a 第一張）。`docker-compose.yml` 新增四個 service（`authentik-postgres` postgres:16-alpine、`authentik-redis` redis:7-alpine、`authentik-server` ghcr.io/goauthentik/server:2024.12、`authentik-worker` 同 image command=`worker`）+ 三個 named volume（`authentik_postgres_data`、`authentik_redis_data`、`authentik_media`）。Volume 走 named 不走 bind-mount 是刻意的——`planning/devops/authentik-stack.md` §2.1 + STATUS.md S3-3 都已記錄 bind-mount 跨 worktree 污染的歷史。Authentik 用自己的 postgres / redis instance（非共用主 app 的）走的也是 planning §3 isolation 原則——namespace 衝突 + blast radius 隔離。`nginx` `depends_on` 加上 `authentik-server`（純 startup ordering，沒設 `condition: service_healthy`，避免主 stack 卡在 authentik 60s start_period 內）。`docker-compose.override.yml` dev overlay 暴露 `authentik-server` `:9000` / `:9443` 到 host（akadmin 首登 token flow 需要直接 admin UI 存取；Phase 1 prod 走 nginx /oauth/ 反代不對外開那兩個 port，per ticket §Notes）並設 `AUTHENTIK_LOG_LEVEL: debug`。`infra/nginx/nginx.conf` 加 `authentik_upstream` + `location /oauth/`，`proxy_pass http://authentik_upstream/`（trailing slash 把 `/oauth/` 前綴 strip 掉，這樣 `curl http://localhost/oauth/-/health/ready/` 對應到 `authentik-server:9000/-/health/ready/` 通用 health endpoint）；sub-path hosting 有已知 Authentik UI quirk（emit 絕對連結 ignore `/oauth` prefix），T-053 落地若撞到再切 subdomain `auth.character-foundry.local`（planning §3.1 已有後路）。`.env.example` 加 `AUTHENTIK_SECRET_KEY`（`openssl rand -base64 32`）+ `AUTHENTIK_POSTGRES_PASSWORD`（與主 app `POSTGRES_PASSWORD` 分開——這是兩個獨立 DB instance、不能共用密碼），`planning/devops/environment-variables.md` 補新 §2.8 Authentik table + §3 `.env.example` 模板同步。Image pin 取 `2024.12`（不用 `latest`——build 可重現；planning §3 範例用 latest 是 sketch，infra 落地以 pin 為準）。Verification 本地缺 Docker daemon 沒辦法跑完整 `docker compose up` 端對端，但 `docker compose config --quiet` clean、`docker compose config --services` 列出全 10 個 service（既有 6 個 + 新 4 個）正確 merge；CI e2e job 走 `docker-compose.yml` base（override 不掛因為 mount 主 repo 路徑）會把新 service 全跑起來，所以四個 Authentik AC 中的「stack 全綠 + named volume 存在 + 設定持續」會在 PR CI 上覆蓋，剩「`/oauth/-/health/ready/` 回 200」需要使用者本地 docker 跑一輪 spot-check（PR body 列入 manual verification checklist）。**不**做：IdP / client 註冊（T-053）、backend / frontend OAuth wire-up（T-054 / T-056）、Authentik 升級流程文件（M3.5 ship 前再開單）。Pre-push review chain (T-062 trigger 命中) 兩個 fixup：(a) **新增 `authentik_certs:/certs` named volume** mount 到 server + worker——OIDC/SAML signing key 存這、不持久就每次 restart 全部 rotate、AC #4 (down/up 設定持續) 直接 silently 違反，engineering-code-reviewer 抓到；(b) **nginx `/oauth/` X-Forwarded-* 從 `$proxy_add_x_forwarded_for` 改成 `$remote_addr`**——前者 append 客戶端送的 header，被偽造 spoof Authentik 看到的 source IP / cookie-secure scheme，security review 抓到（`/api/` + `/storage/` 既有 block 同毛病但 T-052 不擴大 scope）；(c) **dev override port `127.0.0.1:9000` / `127.0.0.1:9443`** loopback bind，T-052 → T-053 之間 bootstrap-setup 暴露窗口縮到 localhost-only；bootstrap env vars (`AUTHENTIK_BOOTSTRAP_PASSWORD` 等) 不加——ticket §Notes 已明寫 admin setup 是 T-053 scope。Review pushback：(1) engineering-reviewer 提 `.env.example` 新 placeholder `change_me_base64_32` 可能不被 gitleaks 涵蓋——核完 `.gitleaks.toml` 第 43 行 regex `change_me(_[a-z0-9]+)*` 已 match，false alarm；(2) security 建議加 `AUTHENTIK_BOOTSTRAP_*` env 鎖死 admin 初始密碼，避免 race——ticket §Notes 明寫 admin setup 屬 T-053，本單只給 perimeter，bootstrap secret 由 T-053 決定（locally 已用 loopback bind 把 race window 砍到沒人能搶）；(3) 圖片 pin `2024.12` 是 LTS rolling minor 不是 digest，留個 inline 註解說明刻意——digest pin 排 M3.5 ship-prep。其他 reviewer 建議（authentik-redis 加 `--requirepass`、postgres `listen_addresses`、`/oauth/` WS upgrade、`depends_on: service_healthy`、`read_only` / `cap_drop`）全 defer，理由各別 inline 或在 T-053 / harden-compose follow-up 處理。Previous: T-066 opened + landing: provider contract replay cadence 從 nightly cron 改 manual-only。觸發點：T-058 nightly 首次 fire 在 2026-05-13 03:00 UTC 因 GitHub repo 沒設 `PROVIDER_CONTRACT_OPENAI_KEY` / `PROVIDER_CONTRACT_VEO_KEY` 兩個 Actions secret 紅了，自動開 `provider-drift` issue #83；操作者 audit cost model 後（gpt-image $0.01 + gpt-5-mini < $0.01 + Veo i2v 3s ~$0.30 / run ≈ $10/月 monthly burn）決定 cron 對單人內部專案不划算停掉。改動：`.github/workflows/provider-contract.yml` 移除 `on.schedule` 整個 block，保留 `workflow_dispatch`（也順手修一條 `operations.md §8` → `§7.3` 的 stale doc 引用）；`planning/devops/operations.md` §7 整段重寫——標題從「Provider contract replay 維運 SOP」上方加 cadence 註腳、§7.3 從「Spending cap 設定」改成「觸發時機與 spending cap」並列每種 manual trigger 場景（PR 動到 `app/ai/*` client / 懷疑 provider schema drift / retro spot check）、cost table 從 monthly 改 per-run（$0.32/run，$5 cap 容 ~15 runs/month）、§7.4 三選一 triage 流程不變、§7.5 加恢復 nightly 的 escape hatch；`planning/harness/scope.md` §2.3 Behaviour 行（real provider replay：「無」→「on-demand manual-only since T-066」）、§2.6 lifecycle table（post-integration / nightly 改 `mutation.yml` only + provider-contract 移到 manual）、§3 gap 行 #2 / #4 加 T-066 變更註記、§3 gap 行 #4 從「nightly job 沒有 cron 設好」改成「cadence 分兩條：cheap auto-sensor 每晚、expensive real-call sensor 按需」；`planning/harness/roadmap.md` §1 A1 標題從「Nightly 真 provider contract replay」改「真 provider contract replay (manual-only since T-066)」、§4 時程圖 T-058 行同步、Owner trigger 行加 T-066 link。Contract test 程式碼 (`api/tests/ai/test_real_provider_contract.py`) **完全沒動**——drift 偵測邏輯保留、`_require_env` skip 行為仍在、Veo Shape A/B 接受邏輯仍在；T-066 純粹只改 cadence + 文件對齊。Workflow 內 `if: github.event_name == 'schedule'` secret 預檢步驟保留為 dormant guard（schedule 復活時自動 re-activate；manual dispatch 不觸發此 step，per-test `pytest.skip()` 接管 missing-key handling）。Issue #83 在 PR merge 後 close 並貼合連結（cron 不再 fire、72h dedup window 內也不會再被 touch，留 open 純噪音）。Previous: T-063 implemented: `CF_SKIP_REVIEW=1` audit log (Harness A6, last A-tier card). Both pre-push hooks (`.claude/hooks/pre-push-review.sh` PreToolUse + `.githooks/pre-push` terminal) gained an identical `append_skip_log` shell helper that fires from the bypass branch and appends one JSONL line per honored bypass to `.harness/skip-review.log` — fields `ts` (ISO8601 UTC), `branch` (`git symbolic-ref --short HEAD`), `range` (`@{u}..HEAD` with `origin/main..HEAD` fallback when no upstream), `reason` (`CF_SKIP_REVIEW_REASON` env, optional), `source` (`claude-hook` vs `terminal-hook` to disambiguate which gate fired). JSON-string escape is bash-only (`${var//\\/\\\\}` then `${var//\"/\\\"}` then raw-newline / tab → space) so the schema stays jq-parseable without a jq dependency; verified locally against `'with "quotes" and \backslash and ☃️'` reason. Failure to write the log is non-fatal (`>> "$log" 2>/dev/null || true`) — push semantics never block on observability. `.harness/skip-review.log` added to `.gitignore` (local accumulation only, never sync'd; bypass cadence is personal-dev rhythm, not PR content). `.harness/README.md` documents both `mutation-baseline.json` (tracked, T-060) and `skip-review.log` (gitignored, T-063) with retro-time `jq` recipes for bypass-rate trend, reason distribution, and non-docs/-fix branch misuse spotting. Manual verification matrix: both hooks called with bypass → 2 lines written, both `python -m json` parse clean; non-bypass path → exit unchanged, zero log writes (gate still denies via Claude / exits 1 in terminal hook); `git check-ignore -v` confirms `.gitignore:79` blocks the log. Sprint 3.5-pre A-tier now 100% complete (T-058 / T-059 / T-060 / T-061 / T-062 / T-063 all DONE) — sequencing block on Sprint 3.5a OAuth series is lifted; T-052 / T-055 can start in parallel next. Previous: T-062 implemented: subagent stack — `security-engineer` + `db-optimizer` 加進 `.claude/agents/`（fork 自 `msitarzewski/agency-agents`，per-file source 標註），pre-push hook 改造為 ticket-aware chain trigger。`.claude/hooks/pre-push-review.sh`（Claude PreToolUse）與 `.githooks/pre-push`（terminal）兩個 mirror hook：從 `git symbolic-ref --short HEAD` 抽 `T-XXX`，讀 `tickets/T-XXX-*.md` / `tickets/DONE/T-XXX-*.md`，先 `grep -v '^## OAuth scope required'` 把樣板 section header 過濾掉（template 注入每張票一行 `## OAuth scope required` 否則 OAuth 關鍵字會在 docs / CI / planning ticket 上偽陽——T-064 即觸發），然後跑兩條 case-insensitive ERE：（1）SEC=`security-sensitive|oauth|\bjwt\b|\bpkce\b|bearer token|authentik|client_secret|refresh[._ ]token|scope decorator|secret scan|sast`；（2）DB=`alembic|alter table|add column|drop column|schema migration|migration script|\bbackfill\b|new index|enum column`。命中 SEC 在 Claude 端的 JSON `additionalContext` 內插 `\n\nADDITIONAL (security-sensitive ticket T-XXX): also run subagent_type=security-engineer...`；命中 DB 同樣插一條 db-optimizer directive；terminal hook 對應在 deny EOF 之間 `printf` 兩段 `ADDITIONAL` 區塊。Hook 自己沒能力 spawn subagent——directive 只是 PreToolUse deny message 內文，由 Claude 看到後決定執行；這是「人 = decision-maker」的延伸（已寫進 CONTRIBUTING §4.4）。Acceptance criterion 3 手動 simulation：T-052（infra）→ SEC=1 DB=0、T-053（security）→ SEC=1 DB=0、T-055（schema migration）→ SEC=1 DB=1、T-056（frontend）→ SEC=1 DB=0，全部與設計一致；T-051 / T-058 / T-064 三張 non-OAuth / non-schema 票全 0 negative control 也通過（過濾 `## OAuth scope required` 之前 T-064 因樣板 header 偽陽——這是修進來的原因）。JSON 組裝走 `cat <<'EOT'` heredoc 不展開 + bash `${TEMPLATE/'{{EXTRA}}'/$EXTRA}` placeholder 替換，避開 backtick command-substitution 在反引號滿滿的 directive 內爆 bash。新 subagent `.md` frontmatter 與既有 `engineering-code-reviewer.md` 對齊（`name` / `description` / `tools: Read, Glob, Grep, Bash, WebFetch`），body 走 priority-marker 慣例（🔴 / 🟡 / 💭），檔尾 source footer 指向上游 `engineering/engineering-security-engineer.md` / `engineering/engineering-database-optimizer.md`。`CONTRIBUTING.md` §4.4 從 generic 三項清單升級成 chain-trigger 表格，明寫每個 agent 何時被 hook 自動建議 chain。Previous: T-061 implemented: secret scan + SAST baseline (Harness A4). Adds `gitleaks` (pre-commit hook + parallel PR-CI `security` job), `bandit -c pyproject.toml -r app/ -ll`, and `semgrep scan --config p/owasp-top-ten` against `api/app/`. `bandit[toml]>=1.7` + `semgrep>=1.50` added to api dev extras alongside `[tool.bandit] exclude_dirs = ["tests", "alembic"]`; `gitleaks.toml` extends the default ruleset (`useDefault = true`) plus a tight allowlist for `change_me*` / `ci_*_not_for_prod_*` placeholders that already live in `.env.example` and the e2e workflow heredoc (file-level ignores explicitly forbidden — see `.gitleaksignore` header). `.semgrepignore` carves out tests / alembic / mutants / node_modules / pnpm-store / frontend (frontend SAST defers to a later ticket per T-061 §"Not in scope"). The new `security` workflow job runs in parallel with `backend-lint-test` + `frontend-lint-test` (no `needs:`), so wallclock impact ≈ bandit + semgrep (≈ 2 min in steady state, sub-20 % of existing PR CI). Local baseline run on 2026-05-13: bandit `-ll` clean over 13 355 LOC, semgrep clean on 119 files / 153 OWASP rules, gitleaks clean on staged tree (and verified-positive against a planted `sk-proj-T3BlbkFJ-…` blob via `pre-commit run gitleaks --files` — hook exited 1 with `RuleID: generic-api-key`, confirming acceptance criterion). T-053 already lists `Depends on: T-061` so the hard-sequencing gate is in place without further ticket edits. Previous: T-060 implemented: coverage gate + nightly mutation testing (Harness A3). PR CI now enforces `fail_under = 75` in `[tool.coverage.report]` (`api/pyproject.toml`) — 2 pp below the 2026-05-12 backend baseline of 77 % (5787 stmts / 1307 missed) so legitimate variance from excluded lines doesn't false-alarm; the `pytest` step in `.github/workflows/pr.yml` keeps its `--cov=app --cov-report=term --cov-report=xml` flags and now also uploads `coverage.xml` as a 14-day artifact for spot-checks. Mutation testing scope landed for the two trust-bearing pure-Python modules: `app/core/errors.py` (AgentError envelope) + `app/ai/circuit.py` (fail-safe behavior). Initial baseline kill rate **79.84 % (99 killed / 124 evaluable)** committed to `.harness/mutation-baseline.json` with a 5 pp drift threshold. Nightly workflow `.github/workflows/mutation.yml` runs at 03:00 UTC (staggered 3 h after T-058's provider-contract sweep so they don't fight Actions slots), shells out `mutmut run` + `mutmut export-cicd-stats`, then `api/scripts/check_mutation_drift.py` compares against the baseline JSON and opens / comments on a `mutation-drift`-labelled issue when kill rate drops more than 5 pp (72 h sliding-window dedup + `pull_request` filter mirroring T-058's pattern). `app/auth/*` mutation **defers**: `tests/auth/conftest.py` lazy-imports `app.main` → ORM models → `pgvector` → numpy, which collides with mutmut 3.5's in-process trampoline approach (`ImportError: cannot load module more than once per process` reproduced 2026-05-12); `pytest-forked` passes tests but kills the `mutate_only_covered_lines` stats collection because forked subprocesses don't report trampoline hits. Full reproduction + two candidate fixes (cosmic-ray switch, conftest lazy-import) documented in `api/pyproject.toml` `[tool.mutmut]` comments and `planning/harness/scope.md` §2.5 known-gap appendix. New backlog row S3.5-2 tracks the follow-up. Other deltas: `mutmut>=3.0` added to api dev extras, `mutants/` + `api/.coverage` + `api/coverage.xml` + `api/drift-report.txt` ignored, `docker-compose.override.yml` bind-mounts `.harness/` + `api/scripts/` + `api/pyproject.toml` into the api container so the README's docker-compose-exec repro path resolves the baseline correctly. Previous: T-059 implemented: architecture fitness / layering test (Harness A2). Adds `import-linter>=2.0` to api dev deps and `[tool.importlinter]` to `api/pyproject.toml` with two active forbidden contracts: (1) `app.api.*` may not directly import `app.models.*` — `allow_indirect_imports = true` so the sanctioned `route → repository → model` chain stays legal; 14 existing edges live in `ignore_imports` split into two annotated buckets (10 `User`-as-auth-context exceptions through `Depends(get_current_user)`, 4 real `characters.py` / `tasks.py` leaks tracked by new STATUS.md backlog row S3.5-1); (2) `app.ai.*` may not import `app.api.*` (currently zero violations). A commented M3.5 placeholder contract documents the shared-scope-source rule that activates with T-054. `api/tests/arch/test_layering.py` shells out to `lint-imports --config pyproject.toml --no-cache` (resolved via `shutil.which`) so PR CI's existing pytest step covers the check — no new workflow needed; failure message embeds the broken contract block plus actionable fix hints per offending edge. Companion `test_oauth_scope_source_is_centralized` is `pytest.skip`-gated on `app/auth/scopes.py` existence: T-054 creating that module auto-flips the skip into a live ast-free string scan (`"character:read"` / `"character:write"` / `"task:read"` / `"task:cancel"` / `"usage:read"`) over `app/**.py`, failing if any non-canonical file hard-codes a scope literal. Verified negative paths locally: removing an ignore entry surfaces the expected actionable diagnostic; creating a temp `app/auth/scopes.py` + non-canonical literal trips the placeholder test correctly. `planning/harness/scope.md` §2.5 (Architecture fitness 零 → 部分) and §3 gap #1 updated to reflect new baseline. Previous: T-058 implemented: nightly real-provider contract replay sensor (Harness A1). Adds `@pytest.mark.real_provider` (registered + default-skipped via `addopts = -m "not real_provider"` in `api/pyproject.toml`), three shape-only contract tests in `api/tests/ai/test_real_provider_contract.py` against gpt-image-2 / gpt-5-mini / Veo 3.1, plus 9 drift-detection tests that exercise the same shape assertion functions on fabricated payloads (no network). Veo accepts Shape A (`done=true` + `response.videos[]` or nested `generateVideoResponse.generatedSamples[]`) OR Shape B (`done=true` + `raiMediaFilteredCount >= 1` + `raiMediaFilteredReasons: list[str]`; T-051 RAI shape) — explicitly NOT a single "videos must exist" invariant. New `.github/workflows/provider-contract.yml`: cron `0 0 * * *` UTC + `workflow_dispatch`, dedicated `PROVIDER_CONTRACT_OPENAI_KEY` + `PROVIDER_CONTRACT_VEO_KEY` secrets (NOT shared with dev), on failure auto-creates `provider-drift` labeled issue via `actions/github-script` with truncated 60 KB pytest log (sed-redacted for `sk-*` / `AIza*` bearer tokens). Triage SOP in `planning/devops/operations.md` §7 covers test-key registration, $5/month spending cap per provider, three-way drift triage (real drift / transient 5xx / infra). `gpt-5-mini` test budget bumped from 64 → 512 `max_completion_tokens` after empirical probe showed the reasoning model burned the 64-cap on `reasoning_tokens` before emitting `content`. Previous: T-051 opened: Veo 3.1 RAI filter 走「`done: true` 但無 videos」shape，目前被 `_fetch_video_bytes` 誤分類為 `MODEL_INVALID_REQUEST` 並硬塞「returned 4xx」字串（user 真機 task `371fc9a8` 命中）。Google 已知 RAI false positive 多（`googleapis/js-genai#1272`）。本單會偵測 `raiMediaFilteredCount` / `raiMediaFilteredReasons`、新增 retryable 的 `MODEL_CONTENT_FILTERED`、加 post-submit RAI retry 小預算（env `VEO_RAI_MAX_RETRIES` default 2），並 audit 修掉 `model_invalid_request` template 在非 4xx 路徑硬塞「returned 4xx」的 5 個誤報點。Previous: T-050 merged (#69, commit 05a0ceb) reconciler prompt tuning vs OpenAI image-gen cookbook: SYSTEM_PROMPT全面 rewrite 注入 cookbook 5 大 prompting 原則 (structure / photographic vocab / people hints / literal text / edit preservation)；`platform_constraints.yaml` v1.1 → v1.2 新增 `base_creation_avoid` + `alias_creation_avoid` block; `menu_fragments.py` style 4 個 option 從單行擴成完整描述（lens / lighting / texture）；final prompt 組裝順序改成 cookbook 推薦的 scene → menu → note → avoid。Scope 限定 gpt-image 路徑 (base / alias)，motion / i2v 端 prompt tuning 之後另開單。Cache via `_logic_version` + `constraint_version` 自動失效（含 YAML payload，防止 wording 改動忘 bump version）。Codex review 跑了 3 round（rule-0 reference / YAML hash / double-period strip + period-then-whitespace ordering），都採納。Previous: T-044 closes T-042's last follow-up by adding `tests/ai/test_gpt_image_2_contract.py`.
> **Phase:** Sprint 1 done（T-006 ~ T-012 全部 done，M1 達成）；Sprint 2 done（T-013 ~ T-028 全部 done，M2 達成）；**Sprint 3 done（T-029 ~ T-041，13 張全部 done，M3 達成）**

---

## Current state

**Planning phase：** ✅ 完成（product / ux / data / backend / frontend / devops 全收斂）
**Implementation phase：** 尚未開工

---

## Sprint progress

### Sprint 0 — Infrastructure
**目標：** `docker compose up` 能跑起整套 stack，hello world 有回應。

| # | Ticket | Status |
|---|---|---|
| T-001 | Repo scaffolding | DONE |
| T-002 | Alembic + initial migrations (teams, users) | DONE |
| T-003 | Remaining migrations (characters → tasks) | DONE |
| T-004 | CI workflow (PR checks) | DONE |
| T-005 | StorageBackend interface + LocalFilesystemBackend | DONE |

### Sprint 1 — Auth + App Shell
**目標：** Login 能成功，看到空 Dashboard。

| # | Ticket | Status |
|---|---|---|
| T-006 | Backend auth (JWT login/refresh/logout/me) | DONE |
| T-007 | Frontend scaffolding (Vite + shadcn init) | DONE |
| T-008 | Frontend auth (login page + store + guard) | DONE |
| T-009 | Backend /health + /v1/meta | DONE |
| T-010 | Frontend TopNav + DegradedBanner | DONE |
| T-011 | Frontend Toast + ErrorBoundary | DONE |
| T-012 | E2E smoke test (login flow) | DONE |

### Sprint 2 — Character Creation
**目標：** 建 Character、選單 / 參考圖模式、Checkpoints、確立 Base（M2）。

| # | Ticket | Status |
|---|---|---|
| T-013 | Backend task queue (arq + Redis) + Task API | DONE |
| T-014 | Backend AI client infra (gpt-image-2 + circuit breaker + stub) | DONE |
| T-015 | Backend Prompt Reconciler module (gpt-5-mini) | DONE |
| T-016 | Backend Character CRUD + CreationSession bootstrap | DONE |
| T-017 | Backend Checkpoint generation flow | DONE |
| T-018 | Backend Select Base / Fork / Abandon | DONE |
| T-019 | Backend Prompt preview endpoint | DONE |
| T-020 | Frontend Dashboard (grid + empty state) | DONE |
| T-021 | Frontend New Character page (mode picker) | DONE |
| T-022 | Frontend Creation Session — template mode | DONE |
| T-023 | Frontend Creation Session — reference mode | DONE |
| T-024 | Frontend Prompt preview modal (M-01) | DONE |
| T-025 | Frontend Select Base + Character Detail (Base only) | DONE |
| T-026 | E2E Character creation smoke test (template) | DONE |
| T-027 | CharacterDetail DTO + frontend resume in-progress session | DONE |
| T-028 | Worker post-lock checkpoint guard（從 T-018 PR #23 拆出來，Codex round-2 P1） | DONE |

### Sprint 3 — Aliases + Motions
**目標：** 三合一 Alias 輸入（含 Inpaint）、Preset + Custom motion，跑完 M3 milestone。

| # | Ticket | Status |
|---|---|---|
| T-029 | Backend Veo 3.1 i2v client + stub | DONE |
| T-030 | Backend gpt-image-2 image2image + inpaint extension | DONE |
| T-031 | Backend Alias generation endpoint + worker | DONE |
| T-032 | Backend Alias list / detail / rename / delete | DONE |
| T-033 | Backend Motion generation endpoint + worker | DONE |
| T-034 | Backend Motion list / detail / rename / delete | DONE |
| T-035 | Backend Prompt preview extension（alias / motion mode + MaskInput schema）| DONE |
| T-036 | Frontend Alias edit page (P-06) + InpaintCanvas | DONE |
| T-037 | Frontend Character Detail aliases + motions sections | DONE |
| T-038 | Frontend Motion preset generation（click-to-generate + SSE）| DONE |
| T-039 | Frontend Custom motion modal (M-02) | DONE |
| T-040 | Frontend Prompt preview modal extension（alias / motion mode）| DONE |
| T-041 | E2E Alias creation + motion preset smoke（M3 gate）| DONE |
| T-042 | Fix gpt-image API contract on real provider（drop dall-e-3 params + multi-image `image[]`） | DONE |
| T-043 | Sync `planning/backend/ai-integration.md` to real gpt-image contract（T-042 follow-up） | SUPERSEDED by T-048 |
| T-044 | Outgoing-body contract test for gpt-image client（T-042 follow-up） | DONE |
| T-045 | Fix reconciler client for gpt-5-mini contract drift（max_completion_tokens + drop temperature=0）| DONE |
| T-046 | Shared `/storage` volume + nginx `/storage/` proxy（image preview broken bug）| DONE |
| T-047 | Aspect-ratio dropdown + framing guidance（head cropping fix）| DONE |
| T-048 | Sync planning docs（T-042 / T-045 / T-046 / T-047）+ yaml bind-mount in dev override | DONE |
| T-049 | Require e2e happy path for routing / new-page / critical-action PRs（process gate）| DONE |
| T-050 | Reconciler prompt tuning vs OpenAI image-gen cookbook（gpt-image only；i2v 之後另開單） | DONE |
| T-051 | Veo 3.1 RAI filter 偵測 + 修 `model_invalid_request` template 誤導性「returned 4xx」字串 | DONE |

**Dependency / parallelization plan：** 見 `tickets/PARALLEL_WORKFLOW.md`。Wave A（T-029 / T-030 / T-035 / T-036 / T-040）可立即平行開工。

### Sprint 4 — Download + Usage（尚未開單）
ZIP 匯出、Copy Character、Usage dashboard。

### Sprint 5 — Polish（尚未開單）
剩餘錯誤處理、E2E coverage、效能調整。

### Sprint 3.5 — Agent-native baseline（plan phase 完成 2026-05-07，3.5a 已開單）
**目標：** OAuth 2.1（替換 JWT）+ MCP server，外部 agent 不看 REST 文件就能跑全流程。
**規劃：** ✅ 4-step plan phase 全部完成（2026-05-07）。

> **2026-05-12 sequencing 決定（使用者）：** Sprint 3.5a OAuth 系列**整體 blocked on Sprint 3.5-pre harness 全完成**。Harness 蓋完才開始做 M3.5——避免 OAuth + MCP 兩個新 layer 在沒 guardrail 的狀態下落地。詳見 `planning/harness/`。

#### Sprint 3.5-pre — Harness pre-flight（已開單 2026-05-12，未動工）

對照 Martin Fowler "Harness Engineering for Coding Agents"，由 Harness Agent 規劃。完整 rationale 見 `planning/harness/roadmap.md`。

| # | Ticket | Status |
|---|---|---|
| T-058 | 真 provider contract replay sensor（A1；manual-only since T-066）| DONE |
| T-059 | Architecture fitness — layering / import-direction test（A2）| DONE |
| T-060 | Coverage gate + mutation testing on critical modules（A3）| DONE |
| T-061 | Secret scan + SAST baseline（A4；**T-053 之前必 land**）| DONE |
| T-062 | Subagent stack — security-engineer + db-optimizer（A5）| DONE |
| T-063 | `CF_SKIP_REVIEW=1` audit log（A6）| DONE |

**Dependency / parallelization：**
- T-058 / T-059 / T-060 / T-062 / T-063 五張無內部 dep，可全 wave 平行
- T-061 也無內部 dep，但**對下游 T-053 是 hard blocker**
- 全部 land 後才解 Sprint 3.5a OAuth 系列的 sequencing block

#### Sprint 3.5a — OAuth migration（已開單，未動工；blocked on Sprint 3.5-pre）

| # | Ticket | Status |
|---|---|---|
| T-052 | Authentik docker service 加入 stack | DONE |
| T-053 | Authentik 設定 Google upstream IdP + client 註冊 | DONE |
| T-054 | Backend dual-stack auth middleware（JWT + OAuth） | TODO |
| T-055 | `refresh_token` table 加 `token_source` 欄位 | TODO |
| T-056 | Frontend Sign in with Google + AuthCallbackPage + authStore dual-stack | TODO |
| T-057 | E2E OAuth login smoke + dual-stack 並存測試（ship gate） | TODO |

**Dependency / parallelization：**
- 整個 Sprint 3.5a blocked on Sprint 3.5-pre 全完成（2026-05-12 決定）
- 解 block 後：T-052 / T-055 可平行起步（無內部 dep）
- T-053 等 T-052 **且** T-061（A4 secret scan）已 merge — ✅ both gates met before T-053 land；T-054 等 T-055 + T-053
- T-056 等 T-054；T-057 等 T-056

#### Harness B-tier follow-ups（M3.5 ship 後再排；可隨時插單，不 block Sprint 3.5a）

| # | Ticket | Status |
|---|---|---|
| T-064 | Provider-drift issue dedup by failure signature（T-058 round-3 defer；T-066 後 priority 下調）| TODO |
| T-065 | PR CI guard — `[tool.mutmut]` change must bump `.harness/mutation-baseline.json`（T-060 enforcement upgrade）| TODO |
| T-066 | Provider contract replay 改 manual-only（停 nightly cron，~$10/月成本砍）| TODO |
| T-067 | Harden docker-compose secret interpolation + minimal container posture（T-052 PR #85 Codex P1 + security review batch defer）| TODO |

#### Sprint 3.5b / 3.5c — 未開單（3.5a ship 完再開）

**Plan phase deliverable：**
- `planning/agent-interface/open-questions.md` — Round 1/2/3 決策紀錄（9 條全鎖）
- `planning/auth/open-questions.md` — 決策紀錄（8 條全鎖）
- `planning/backend/oauth-mcp-integration.md` — scope decorator + MCP tool registry + CI 護欄
- `planning/frontend/oauth-integration.md` — login UI + authStore dual-stack
- `planning/devops/authentik-stack.md` — Authentik docker stack + persistence
- `tickets/_TEMPLATE.md` — 新增「OAuth scope required」+「MCP tool delta」section

**關鍵決策（high level）：**
- OAuth provider：Authentik (OSS) + Google Workspace 當 upstream IdP
- Grant types：delegation（Auth Code + PKCE）+ M2M（Client Credentials）並存
- Scope：5 條（`character:read/write` / `task:read/cancel` / `usage:read`）+ narrow default + per-client 覆寫
- Signed URL：維持獨立 JWT，與 OAuth 解耦
- MCP transport：streamable HTTP, same-process FastAPI sub-app `/mcp`
- Client 註冊：pre-registered allowlist（Figma 模式），DCR 不開
- Migration：簡化 dual-stack，1 sprint 完成

---

## Milestones

- [ ] **M0** — Dev environment runs（`docker compose up` → `/health` returns ok）【Sprint 0 完成】
- [x] **M1** — Login works end-to-end【Sprint 1 完成】
- [x] **M2** — Create Character (template mode) end-to-end【Sprint 2 完成】
- [x] **M3** — Aliases + Motions working【Sprint 3 完成】
- [ ] **M3.5** — Agent-native baseline：OAuth 2.1 + MCP server，外部 agent 能不看 REST 文件跑全流程【2026-04-30 從 Phase 2 拉回 Phase 1；詳見 `planning/agent-interface/`、`planning/auth/`】
- [ ] **M4** — Download ZIP works【Sprint 4 完成】
- [ ] **M5** — First internal user feedback【Sprint 5 完成】

---

## 開新 ticket 時更新這張表

- 新單：加進對應 sprint 區塊
- Status 改：同步更新這張表的狀態欄
- 完成：移進 DONE（`git mv`）+ milestone 若符合就勾

---

## Known risks / deferred items

| # | Item | 處理時機 |
|---|---|---|
| M5 | Dropdown 選項實際內容 | 實作時平行填充 |
| M7 | 錯誤 UX 細節訊息 | Frontend 實作時對照真 backend 回應 |
| M8 | Lip sync 延後是未驗證的賭注 | Phase 1 demo 前做 5 人快速 check |
| FB-3 | Storage URL expired 時 backend 要回對的 code | ✅ T-005 完成（`STORAGE_URL_EXPIRED` vs `AUTH_INVALID_TOKEN` 已分開） |
| - | Visual design (Pencil mockup) | 之後需要再開 UX iteration 3 |
| S2-1 | Slug-based URL（目前 `/characters/:id`）| Sprint 3/4 衡量 SEO/可分享性需求再做 |
| S2-3 | Dashboard 分頁 / infinite scroll（T-020 首版用 `limit=100` 平鋪，未做 cursor pagination）| Character 數逼近 100 或 UX 反饋時 |
| S2-4 | `Checkpoint` DTO 不含 `menu_selections` / `freeform_note`，所以 server-loaded checkpoint 點 `[用這張再改]` 無法 prefill form（T-022 placeholder 期間靠 client-side 記憶；reload 後就只設 remix base、form 留白）| Backend 加欄位後 Frontend 移除 placeholder fallback |
| S2-6 | `BaseDTO` 缺 prompt 欄位（`menu_selections` / `freeform_note` / `prompt_summary`），所以 Character Detail 上的「查看完整 prompt」modal 只能顯示 source checkpoint id + 建立時間，沒辦法重現完整 prompt 組合。T-025 frontend 落地時用 `BasePromptModal` placeholder 暫頂；Backend 在 BaseDTO 加 prompt 欄位後即可改為 reuse PromptPreviewModal。| 開新 ticket 擴充 `BaseDTO` schema |
| S3-2 | T-030 `edit_image2image` 多參考圖的 multipart shape（重複 `image` field name）依 gpt-image-1 公開合約建模；gpt-image-2 假設沿用，但需在 T-031 整合真 provider 前以 smoke 驗證一次 | T-031 production cutover 前 |
| S3.5-1 | Route 層直接 import ORM models 的歷史 leak：4 條真 leak（`routes/characters.py` → Character / CreationSession / BaseAsset、`routes/tasks.py` → Task）+ 10 條 sanctioned User-as-auth-context（routes / deps via `Depends(get_current_user)`）。真 leak 改走 repository helper：`character_repo.get_character_by_id` 等已存在；`base` 與 `creation_session` 需在 `app/repositories/` 新增 `base_repo` / `creation_session_repo` 模組（schemas 已有 `app.schemas.creation_session` / `app.schemas.base`，repo 層補 thin wrapper 即可）。Sanctioned exception 需要 UserContext Pydantic schema 設計：handler 端目前用 `user.id` / `user.team_id`（grep `current_user.\b` 列當前實際使用面，schema 對齊那組欄位即可）。全部列在 `api/pyproject.toml` `[tool.importlinter]` 的 `ignore_imports`（T-059 標註好兩種類別與該怎麼修）。| 每張碰 characters / tasks route 的 ticket 順手清一條；UserContext refactor 開單時統一處理 sanctioned exception |
| S3.5-2 | `app/auth/*` 不在 mutmut scope：`tests/auth/conftest.py` lazy-imports `app.main` → ORM models → `pgvector` → numpy；mutmut 3.5 in-process trampoline 重 import 觸發 numpy 的 "cannot load module more than once" guard，`pytest-forked` 治標但會關掉 `mutate_only_covered_lines` 的 stats 蒐集（forked subprocess 不回報 trampoline hits）。原 T-060 ticket 含 auth/* 範圍，實作時撞牆改 defer，否則 baseline 直接停在 collection error。具體 reproduce + 兩條修法（cosmic-ray、conftest 改成 lazy / 不打 `app.main`）寫在 `api/pyproject.toml` `[tool.mutmut]` 註解。| T-054 dual-stack middleware 落地前評估；M3.5 期間若 auth 模組複雜度升高，優先 promote |
| S3-3 | Docker stack 與多 worktree 結構性錯位：`docker-compose.yml` 的 `./api/app:/app/app` 等 bind-mount 解析永遠指向主 repo（不論你 cwd 在哪 worktree），且整套 stack 全 worktree 共用一份 container；`docker cp` / `docker exec` 寫 `/app/...` 都會反向洩漏到主 repo 工作樹（2026-04-30 T-033 PR #47 開工時踩過）。`tickets/PARALLEL_WORKFLOW.md` §8 已寫 do/don't + T-031 「`docker run --rm -v $WORKTREE/api:/app`」正確 pattern，但這只是約定，沒結構性阻擋。三個可行修法：(a) 維持文件約定；(b) 改 per-worktree compose project name (`docker compose -p`)；(c) 殺掉 bind-mount source 改 image rebuild（破壞 hot-reload）。| M3.5 開工（OAuth provider docker container 進場時 docker stack 表面擴大）；或 Wave C+ 再有 worktree 踩到時 |

---

## 下一個 Session 開工前必讀

1. `CLAUDE.md` — 專案定位 + agent 切換
2. `DECISIONS.md` — 核心決策 quick ref
3. `tickets/T-XXX-*.md` — 本單完整內容
4. 單裡 **Planning refs** 列的檔案
