# Quota Checker Mechanism Resilience & Window Segregation Design

- **Status:** Approved
- **Author:** Antigravity / Promux Team
- **Date:** 2026-10-25
- **Scope:** `pkg/quota`, `pkg/config`, `pkg/cmd`

---

## 1. Context & Motivation

Promux manages multiple Antigravity CLI (`agy`) OAuth profiles with automated failover and quota monitoring. Quotas in Google Cloud Code Assist / Antigravity are governed by two distinct constraints:
1. **A 5-Hour Rolling Limit:** Smooths short-term demand across global clusters.
2. **A 7-Day Rolling Weekly Limit:** Dictated by individual tier allowances (e.g. Claude Opus/Sonnet, GPT-OSS, and in certain tiers, Gemini models).

Recent investigations and live API testing revealed two core resilience issues in the quota checker:
1. **Asymmetric Endpoint Fallback:** In `pkg/quota/client.go`, when `c.BaseURL` is set to `daily-cloudcode-pa.googleapis.com` (as auto-detected from `cli.log` on current systems), the client only checks `daily` and never falls back to production `cloudcode-pa.googleapis.com` if `daily` fails or is throttled.
2. **Gemini 5h Window Clamping Bug:** When per-model metrics from `/v1internal:retrieveUserQuota` report a multi-day weekly reset for Gemini (e.g. 6 days remaining with 38.8% quota), `client.go` updates the weekly quota, but also clobbers `Gemini5hRemaining` down to `0.388` without setting `Gemini5hReset`. This produces confusing outputs like `38.8% (-)` in the 5-hour column, falsely implying the rolling 5-hour capacity was consumed.

This design establishes a permanent, long-term solution that provides symmetric multi-tier URL fallback and strict segregation of rolling and weekly quota windows.

---

## 2. Architecture & Detailed Design

### 2.1 Symmetric Multi-Tier URL Fallback

`QuotaClient.post(endpoint, payload)` must attempt endpoints in prioritized order based on the configured or detected primary URL:

```
                  ┌─────────────────────────┐
                  │ QuotaClient Post Request│
                  └────────────┬────────────┘
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
   Primary is Daily?                     Primary is Prod?
 [daily-cloudcode-pa]                  [cloudcode-pa]
            │                                     │
            ▼                                     ▼
  1. daily-cloudcode-pa                 1. cloudcode-pa
  2. cloudcode-pa                       2. daily-cloudcode-pa
            │                                     │
            └──────────────────┬──────────────────┘
                               ▼
                   Custom URL Configured?
            [e.g. mock server or custom proxy]
                               │
                               ▼
            1. Custom BaseURL
            2. daily-cloudcode-pa
            3. cloudcode-pa
```

#### Fallback Rules
1. **Candidate Resolution:**
   - Candidate 1: `c.BaseURL` (primary).
   - Candidate 2: If `c.BaseURL == FallbackCodeAssistBaseURL`, candidate 2 is `CodeAssistBaseURL`. If `c.BaseURL == CodeAssistBaseURL`, candidate 2 is `FallbackCodeAssistBaseURL`.
   - Candidate 3 (if primary is custom): Both `daily` and `prod` endpoints are appended.
   - All candidate URLs are de-duplicated preserving priority.
2. **Failover Triggers:**
   - Network connectivity errors (DNS resolution, connection refused, connection reset).
   - Client timeouts.
   - HTTP Status 500 (Internal Server Error), 502 (Bad Gateway), 503 (Service Unavailable), 504 (Gateway Timeout).
   - HTTP Status 429 (Too Many Requests / Resource Exhausted on the API endpoint itself).
3. **Fail-Fast Boundaries:**
   - HTTP 401 (Unauthorized) and HTTP 403 (Forbidden) indicate invalid, expired, or prohibited credentials. The client terminates immediately without retrying alternate endpoints.

### 2.2 Window Segregation & Decoupling

In `/v1internal:retrieveUserQuota`, each model reports only its single most constraining bucket:
- **Resets $\le 24\text{ hours}$:** Rolling 5-hour window or 24-hour daily quota exhaustion cooldown.
  - Updates `Gemini5hRemaining` and `Gemini5hReset` for Gemini models.
  - Updates `ThirdParty5hRemaining` and `ThirdParty5hReset` for Claude/GPT models (when reset $\le 6\text{h}$).
- **Resets $> 24\text{ hours}$:** 7-day rolling weekly limit.
  - Updates `GeminiWeeklyRemaining` and `GeminiWeeklyReset` for Gemini models.
  - Updates `ThirdPartyWeeklyRemaining` and `ThirdPartyWeeklyReset` for Claude/GPT models.
  - **Eliminate Clamping:** Do **not** overwrite `Gemini5hRemaining` with weekly fraction. The rolling 5-hour window remains decoupled from the weekly limit, eliminating phantom `X% (-)` entries.

### 2.3 Display Formatting Consistency

- **Gemini Weekly:** If `GeminiWeeklyReset == nil`, rendered as `-`. If active, rendered with relative duration (e.g. `38.8% (6d)`).
- **Third-Party Weekly:** If `ThirdPartyWeeklyReset == nil`, rendered as `100.0% (-)`. If active, rendered with relative duration (e.g. `14.0% (1d 2h)`).
- **5-Hour Windows:** If no active countdown exists, formatted as `100.0% (-)`. If active, formatted with relative duration (e.g. `100.0% (4h 58m)`).

---

## 3. Implementation Plan

1. **`pkg/quota/client.go`**:
   - Refactor candidate URL generation in `post()` to support symmetric fallback between `daily` and `prod`.
   - Update `GetQuota()`: remove the line clamping `qs.Gemini5hRemaining` to `qs.GeminiWeeklyRemaining` on multi-day resets.
2. **`pkg/quota/quota_test.go`**:
   - Add unit tests for symmetric fallback (`daily` -> `prod`, `prod` -> `daily`, and custom URLs).
   - Add unit test verifying HTTP 401/403 breaks without fallback.
   - Add unit test verifying multi-day Gemini resets do not clobber `Gemini5hRemaining` or produce empty reset duration.

---

## 4. Verification Criteria

1. `go test -v -race ./pkg/quota/...` passes.
2. `go test -v -race ./...` passes (0 race conditions, 0 test failures).
3. `go vet ./...` returns 0 issues.
4. `make build` compiles `bin/promux` cleanly.
5. Live test: `./bin/promux quota --all` displays accurate, decoupled 5-hour and weekly quotas across all profiles.
