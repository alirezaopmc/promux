# Quota Checker Mechanism Resilience & Window Segregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement symmetric multi-tier URL fallback (`daily` <-> `prod`), harden error classification to short-circuit fatal auth rejections, and strictly decouple Gemini multi-day weekly limits from rolling 5-hour quota.

**Architecture:** Candidate endpoint URLs will be resolved symmetrically in `pkg/quota/client.go` such that whether `BaseURL` is `daily-cloudcode-pa` or `cloudcode-pa`, requests automatically failover to the peer endpoint upon 5xx, 429, or network errors, while 401/403 fail-fast immediately. The quota enrichment parser will strictly segregate multi-day Gemini resets into `GeminiWeeklyRemaining` without clobbering `Gemini5hRemaining`.

**Tech Stack:** Go 1.24+, standard library (`net/http`, `net/http/httptest`, `strings`, `time`).

## Global Constraints
- Zero runtime dependencies outside standard library and existing direct dependencies.
- Advisory file locking and atomic writes preserved.
- No debug/info logs to `os.Stdout`.
- `go test -v -race ./...` must pass with 0 race conditions and 0 failures.
- `go vet ./...` must pass cleanly.

---

### Task 1: Symmetric Multi-Tier URL Fallback in `pkg/quota/client.go`

**Files:**
- Modify: `pkg/quota/client.go:40-91`
- Test: `pkg/quota/quota_test.go`

**Interfaces:**
- Consumes: `c.BaseURL`, `config.CodeAssistBaseURL`, `config.FallbackCodeAssistBaseURL`, `c.HTTPClient`
- Produces: `getCandidateURLs(baseURL, endpoint string) []string`, symmetric fallback in `c.post(endpoint string, payload any)`

- [ ] **Step 1: Write the failing tests in `pkg/quota/quota_test.go`**

Add tests covering:
1. `TestQuotaClientSymmetricFallback_FromDailyToProd`: Primary is daily; when daily fails with 503, succeeds via prod.
2. `TestQuotaClientSymmetricFallback_FromProdToDaily`: Primary is prod; when prod fails with 503, succeeds via daily.
3. `TestQuotaClientSymmetricFallback_AuthErrorsDoNotFallback`: Primary returns 401; client fails immediately without trying secondary endpoint.

```go
func TestQuotaClientSymmetricFallback_FromDailyToProd(t *testing.T) {
	dailyHits := 0
	prodHits := 0

	prodServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		prodHits++
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"source": "prod"})
	}))
	defer prodServer.Close()

	dailyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		dailyHits++
		http.Error(w, "daily down", http.StatusServiceUnavailable)
	}))
	defer dailyServer.Close()

	// Use helper with custom prod/daily mapping
	client := NewClient("mock-token", dailyServer.URL, 5*time.Second)
	// Override urls generator for test
	urls := getCandidateURLsWithDefaults(dailyServer.URL, "/test", prodServer.URL, dailyServer.URL)
	if len(urls) != 2 || urls[0] != dailyServer.URL+"/test" || urls[1] != prodServer.URL+"/test" {
		t.Fatalf("unexpected candidate urls: %v", urls)
	}
}

func TestQuotaClientSymmetricFallback_Execution(t *testing.T) {
	primaryHits := 0
	fallbackHits := 0

	fallbackServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fallbackHits++
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"result": "ok"})
	}))
	defer fallbackServer.Close()

	primaryServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		primaryHits++
		http.Error(w, "temporary outage", http.StatusBadGateway)
	}))
	defer primaryServer.Close()

	// Client targeting primaryServer URL directly
	client := NewClient("mock-token", primaryServer.URL, 5*time.Second)
	
	// Test candidate url resolution
	candidates := client.candidateURLs("/v1internal:loadCodeAssist")
	if len(candidates) < 2 {
		t.Fatalf("expected at least 2 candidates, got %v", candidates)
	}
}

func TestQuotaClientSymmetricFallback_AuthErrorsDoNotFallback(t *testing.T) {
	primaryHits := 0
	fallbackHits := 0

	fallbackServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fallbackHits++
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"result": "ok"})
	}))
	defer fallbackServer.Close()

	primaryServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		primaryHits++
		http.Error(w, "unauthorized", http.StatusUnauthorized)
	}))
	defer primaryServer.Close()

	client := NewClient("mock-token", primaryServer.URL, 5*time.Second)
	_, err := client.post("/test", map[string]any{})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	if primaryHits != 1 {
		t.Fatalf("expected primary to be hit once, got %d", primaryHits)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v -race ./pkg/quota/... -run TestQuotaClientSymmetricFallback`
Expected: FAIL (compilation error: `candidateURLs` or symmetric mapping not yet defined).

- [ ] **Step 3: Implement candidate URL resolution and symmetric fallback in `pkg/quota/client.go`**

Implement `candidateURLs(endpoint string) []string` on `QuotaClient`:
```go
func (c *QuotaClient) candidateURLs(endpoint string) []string {
	primary := strings.TrimRight(c.BaseURL, "/")
	var candidates []string

	candidates = append(candidates, primary+endpoint)

	var peer string
	if primary == config.FallbackCodeAssistBaseURL {
		peer = config.CodeAssistBaseURL
	} else if primary == config.CodeAssistBaseURL {
		peer = config.FallbackCodeAssistBaseURL
	} else {
		// Custom primary URL: try both daily and prod as secondary fallbacks
		peer = config.FallbackCodeAssistBaseURL
	}

	if peer != "" && peer != primary {
		candidates = append(candidates, peer+endpoint)
	}
	if primary != config.CodeAssistBaseURL && peer != config.CodeAssistBaseURL {
		candidates = append(candidates, config.CodeAssistBaseURL+endpoint)
	}

	// De-duplicate preserving order
	seen := make(map[string]bool)
	var unique []string
	for _, u := range candidates {
		if !seen[u] {
			seen[u] = true
			unique = append(unique, u)
		}
	}
	return unique
}
```

Update `c.post()` to use `c.candidateURLs(endpoint)`:
```go
func (c *QuotaClient) post(endpoint string, payload any) (map[string]any, error) {
	bodyBytes, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	urlsToTry := c.candidateURLs(endpoint)

	var lastErr error
	for _, u := range urlsToTry {
		req, err := http.NewRequest(http.MethodPost, u, bytes.NewReader(bodyBytes))
		if err != nil {
			lastErr = err
			continue
		}

		req.Header.Set("Authorization", "Bearer "+c.Token)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("User-Agent", config.UserAgent)

		resp, err := c.HTTPClient.Do(req)
		if err != nil {
			lastErr = err
			continue
		}

		respBytes, err := io.ReadAll(resp.Body)
		_ = resp.Body.Close()
		if err != nil {
			lastErr = err
			continue
		}

		if resp.StatusCode == http.StatusOK {
			var result map[string]any
			if err := json.Unmarshal(respBytes, &result); err != nil {
				return nil, err
			}
			return result, nil
		}

		lastErr = fmt.Errorf("HTTP %d from %s: %s", resp.StatusCode, u, string(respBytes))
		if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
			break
		}
	}

	return nil, lastErr
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `go test -v -race ./pkg/quota/... -run TestQuotaClientSymmetricFallback`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pkg/quota/client.go pkg/quota/quota_test.go
git commit -m "fix(quota): implement symmetric multi-tier URL fallback between daily and prod"
```

---

### Task 2: Strict Window Segregation and Gemini 5h Clamping Fix in `pkg/quota/client.go`

**Files:**
- Modify: `pkg/quota/client.go:300-311`
- Test: `pkg/quota/quota_test.go`

**Interfaces:**
- Consumes: `uqResp["buckets"]` per-model metrics from `/v1internal:retrieveUserQuota`
- Produces: Segregated `GeminiWeeklyRemaining` without clobbering `Gemini5hRemaining`

- [ ] **Step 1: Write the failing test in `pkg/quota/quota_test.go`**

Add unit test: `TestQuotaClientGeminiWeeklyDoesNotClobber5h`

```go
func TestQuotaClientGeminiWeeklyDoesNotClobber5h(t *testing.T) {
	resetWeekly := time.Now().UTC().Add(6 * 24 * time.Hour).Format(time.RFC3339)

	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1internal:retrieveUserQuotaSummary" {
			_ = json.NewEncoder(w).Encode(map[string]any{"groups": []any{}})
			return
		}
		if r.URL.Path == "/v1internal:retrieveUserQuota" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"buckets": []any{
					map[string]any{
						"modelId":           "gemini-2.5-flash",
						"remainingFraction": 0.388,
						"resetTime":         resetWeekly,
					},
				},
			})
			return
		}
		http.NotFound(w, r)
	}))
	defer mockServer.Close()

	client := NewClient("mock-token", mockServer.URL, 5*time.Second)
	qs, err := client.GetQuota("test-project-123")
	if err != nil {
		t.Fatalf("GetQuota failed: %v", err)
	}

	// 5h remaining must remain 1.0 (unclobbered) and Gemini5hReset must remain nil
	if qs.Gemini5hRemaining != 1.0 {
		t.Fatalf("expected Gemini5hRemaining=1.0, got %f", qs.Gemini5hRemaining)
	}
	if qs.Gemini5hReset != nil {
		t.Fatalf("expected Gemini5hReset=nil, got %v", qs.Gemini5hReset)
	}

	// Weekly remaining must reflect 0.388 and resetWeekly
	if qs.GeminiWeeklyRemaining != 0.388 {
		t.Fatalf("expected GeminiWeeklyRemaining=0.388, got %f", qs.GeminiWeeklyRemaining)
	}
	if qs.GeminiWeeklyReset == nil || *qs.GeminiWeeklyReset != resetWeekly {
		t.Fatalf("expected GeminiWeeklyReset=%s, got %v", resetWeekly, qs.GeminiWeeklyReset)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v -race ./pkg/quota/... -run TestQuotaClientGeminiWeeklyDoesNotClobber5h`
Expected: FAIL (`expected Gemini5hRemaining=1.0, got 0.388000`).

- [ ] **Step 3: Modify `pkg/quota/client.go` to eliminate Gemini 5h clobbering**

In `pkg/quota/client.go`:
```go
					// Multi-day reset (> 24 hours) indicates weekly window
					if *remVal < qs.GeminiWeeklyRemaining || qs.GeminiWeeklyReset == nil {
						qs.GeminiWeeklyRemaining = *remVal
						if resetStr != nil {
							qs.GeminiWeeklyReset = resetStr
						}
					}
```
Remove:
```go
					if *remVal < qs.Gemini5hRemaining {
						qs.Gemini5hRemaining = *remVal
					}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test -v -race ./pkg/quota/... -run TestQuotaClientGeminiWeeklyDoesNotClobber5h`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pkg/quota/client.go pkg/quota/quota_test.go
git commit -m "fix(quota): eliminate gemini 5h remaining clobbering on multi-day resets"
```

---

### Task 3: Full Verification, Binary Compilation and Sanity Check

**Files:**
- Repository-wide test suite

- [ ] **Step 1: Run complete repository test suite**

Run: `go test -v -race ./...`
Expected: PASS (0 race conditions, 0 failures).

- [ ] **Step 2: Run `go vet`**

Run: `go vet ./...`
Expected: 0 warnings, clean exit.

- [ ] **Step 3: Compile binary**

Run: `make build`
Expected: Successfully generates `bin/promux`.

- [ ] **Step 4: Live sanity test**

Run: `./bin/promux quota --all --refresh`
Expected: Overview table renders cleanly without errors or phantom formats.
