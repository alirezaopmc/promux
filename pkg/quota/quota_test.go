package quota

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/models"
)

func TestFormatters(t *testing.T) {
	now := time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC)

	// Past or now
	if got := FormatResetDuration(now, now); got != "now" {
		t.Fatalf("expected 'now', got %s", got)
	}

	// Minutes
	m45 := now.Add(45 * time.Minute)
	if got := FormatResetDuration(m45, now); got != "45m" {
		t.Fatalf("expected '45m', got %s", got)
	}

	// Hours & Minutes
	h2m15 := now.Add(2*time.Hour + 15*time.Minute)
	if got := FormatResetDuration(h2m15, now); got != "2h 15m" {
		t.Fatalf("expected '2h 15m', got %s", got)
	}

	// Days & Hours
	d1h4 := now.Add(28 * time.Hour)
	if got := FormatResetDuration(d1h4, now); got != "1d 4h" {
		t.Fatalf("expected '1d 4h', got %s", got)
	}

	// Percentages
	if got := FormatPercentage(0.854); got != "85.4%" {
		t.Fatalf("expected '85.4%%', got %s", got)
	}
	if got := FormatPercentage(1.0); got != "100.0%" {
		t.Fatalf("expected '100.0%%', got %s", got)
	}
}

func TestQuotaClientMock(t *testing.T) {
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1internal:loadCodeAssist" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"cloudaicompanionProject": map[string]any{"id": "test-project-123"},
				"currentTier":             map[string]any{"id": "tier-pro", "name": "Antigravity Pro"},
			})
			return
		}
		if r.URL.Path == "/v1internal:retrieveUserQuotaSummary" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"groups": []any{
					map[string]any{
						"displayName": "Gemini Models",
						"buckets": []any{
							map[string]any{
								"window":            "5h",
								"remainingFraction": 0.85,
								"resetTime":         "2026-10-16T14:15:00Z",
							},
							map[string]any{
								"window":            "weekly",
								"remainingFraction": 0.95,
								"resetTime":         "2026-10-23T00:00:00Z",
							},
						},
					},
					map[string]any{
						"displayName": "Third-Party Models",
						"buckets": []any{
							map[string]any{
								"window":            "5h",
								"remainingFraction": 0.60,
							},
							map[string]any{
								"window":            "weekly",
								"remainingFraction": 0.75,
							},
						},
					},
				},
			})
			return
		}
		http.NotFound(w, r)
	}))
	defer mockServer.Close()

	client := NewClient("mock-token", mockServer.URL, 5*time.Second)

	meta, err := client.LoadMetadata()
	if err != nil {
		t.Fatalf("LoadMetadata failed: %v", err)
	}
	if meta.ProjectID != "test-project-123" || meta.Tier != "tier-pro" {
		t.Fatalf("unexpected metadata: %+v", meta)
	}

	qs, err := client.GetQuota("test-project-123")
	if err != nil {
		t.Fatalf("GetQuota failed: %v", err)
	}
	if qs.Gemini5hRemaining != 0.85 || qs.GeminiWeeklyRemaining != 0.95 {
		t.Fatalf("unexpected gemini quota: %+v", qs)
	}
	if qs.ThirdParty5hRemaining != 0.60 || qs.ThirdPartyWeeklyRemaining != 0.75 {
		t.Fatalf("unexpected 3p quota: %+v", qs)
	}
	if qs.MinShortWindow() != 0.60 {
		t.Fatalf("expected MinShortWindow 0.60, got %f", qs.MinShortWindow())
	}
}

func TestQuotaCache(t *testing.T) {
	tmpDir := t.TempDir()
	cache := NewCache(tmpDir)

	qs := models.NewQuotaSummary()
	qs.Gemini5hRemaining = 0.75

	// Initially empty
	if _, ok := cache.Get("alpha"); ok {
		t.Fatalf("expected cache miss")
	}

	// Set cache
	if err := cache.Set("alpha", qs); err != nil {
		t.Fatalf("cache.Set failed: %v", err)
	}

	// Read cache
	cached, ok := cache.Get("alpha")
	if !ok || cached == nil {
		t.Fatalf("expected cache hit")
	}
	if cached.Gemini5hRemaining != 0.75 {
		t.Fatalf("expected 0.75, got %f", cached.Gemini5hRemaining)
	}

	// Invalidate
	if err := cache.Invalidate("alpha"); err != nil {
		t.Fatalf("cache.Invalidate failed: %v", err)
	}
	if _, ok := cache.Get("alpha"); ok {
		t.Fatalf("expected cache miss after invalidate")
	}
}

func TestQuotaClientUserQuotaEnrichment(t *testing.T) {
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1internal:retrieveUserQuotaSummary" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"groups": []any{
					map[string]any{
						"displayName": "Claude and GPT models",
						"buckets": []any{
							map[string]any{
								"window":            "5h",
								"remainingFraction": 1.0,
							},
						},
					},
				},
			})
			return
		}
		if r.URL.Path == "/v1internal:retrieveUserQuota" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"buckets": []any{
					map[string]any{
						"modelId":           "gemini-2.5-flash",
						"remainingFraction": 0.85,
						"resetTime":         time.Now().UTC().Add(3 * time.Hour).Format(time.RFC3339),
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
	if qs.Gemini5hRemaining != 0.85 {
		t.Fatalf("expected Gemini5hRemaining=0.85, got %f", qs.Gemini5hRemaining)
	}
	if qs.Gemini5hReset == nil {
		t.Fatalf("expected Gemini5hReset to be non-nil")
	}
}

func TestQuotaClientUserQuotaWeeklyExhaustion(t *testing.T) {
	futureWeekly := time.Now().UTC().Add(48 * time.Hour).Format(time.RFC3339)
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
						"remainingFraction": 0.0,
						"resetTime":         futureWeekly,
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
	if qs.GeminiWeeklyRemaining != 0.0 {
		t.Fatalf("expected GeminiWeeklyRemaining=0.0, got %f", qs.GeminiWeeklyRemaining)
	}
	if qs.Gemini5hRemaining != 1.0 {
		t.Fatalf("expected Gemini5hRemaining=1.0, got %f", qs.Gemini5hRemaining)
	}
	if qs.GeminiWeeklyReset == nil || *qs.GeminiWeeklyReset != futureWeekly {
		t.Fatalf("expected GeminiWeeklyReset=%s, got %v", futureWeekly, qs.GeminiWeeklyReset)
	}
}

func TestQuotaClientUserQuotaMissingResetTime(t *testing.T) {
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
						"modelId":           "gemini-2.5-pro",
						"remainingFraction": 0.70,
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
	if qs.Gemini5hRemaining != 0.70 {
		t.Fatalf("expected Gemini5hRemaining=0.70, got %f", qs.Gemini5hRemaining)
	}
	if qs.Gemini5hReset != nil {
		t.Fatalf("expected Gemini5hReset=nil, got %v", qs.Gemini5hReset)
	}
}

func TestQuotaClientThirdPartyWeeklyDoesNotClobber5h(t *testing.T) {
	reset5h := time.Now().UTC().Add(4 * time.Hour).Format(time.RFC3339)
	resetWeekly := time.Now().UTC().Add(3 * 24 * time.Hour).Format(time.RFC3339)

	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1internal:retrieveUserQuotaSummary" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"groups": []any{
					map[string]any{
						"displayName": "Claude and GPT models",
						"buckets": []any{
							map[string]any{
								"window":            "5h",
								"remainingFraction": 1.0,
								"resetTime":         reset5h,
							},
							map[string]any{
								"window":            "weekly",
								"remainingFraction": 0.25,
								"resetTime":         resetWeekly,
							},
						},
					},
				},
			})
			return
		}
		if r.URL.Path == "/v1internal:retrieveUserQuota" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"buckets": []any{
					map[string]any{
						"modelId":           "claude-sonnet-4-6",
						"remainingFraction": 0.25,
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

	// ThirdParty 5h quota should remain 1.0 and reset5h, NOT overwritten by 0.25 and resetWeekly
	if qs.ThirdParty5hRemaining != 1.0 {
		t.Fatalf("expected ThirdParty5hRemaining=1.0, got %f", qs.ThirdParty5hRemaining)
	}
	if qs.ThirdParty5hReset == nil || *qs.ThirdParty5hReset != reset5h {
		t.Fatalf("expected ThirdParty5hReset=%s, got %v", reset5h, qs.ThirdParty5hReset)
	}
	if qs.ThirdPartyWeeklyRemaining != 0.25 {
		t.Fatalf("expected ThirdPartyWeeklyRemaining=0.25, got %f", qs.ThirdPartyWeeklyRemaining)
	}
	if qs.ThirdPartyWeeklyReset == nil || *qs.ThirdPartyWeeklyReset != resetWeekly {
		t.Fatalf("expected ThirdPartyWeeklyReset=%s, got %v", resetWeekly, qs.ThirdPartyWeeklyReset)
	}
}

func TestQuotaClientGeminiDailyExhaustion(t *testing.T) {
	reset22h := time.Now().UTC().Add(22 * time.Hour).Format(time.RFC3339)

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
						"remainingFraction": 0.0,
						"resetTime":         reset22h,
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

	// 22-hour exhaustion applies to 5-hour/daily limit, NOT weekly reset
	if qs.Gemini5hRemaining != 0.0 {
		t.Fatalf("expected Gemini5hRemaining=0.0, got %f", qs.Gemini5hRemaining)
	}
	if qs.Gemini5hReset == nil || *qs.Gemini5hReset != reset22h {
		t.Fatalf("expected Gemini5hReset=%s, got %v", reset22h, qs.Gemini5hReset)
	}
	if qs.GeminiWeeklyReset != nil {
		t.Fatalf("expected GeminiWeeklyReset=nil, got %v", qs.GeminiWeeklyReset)
	}
}

type roundTripFunc func(req *http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

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
	_ = NewClient("mock-token", dailyServer.URL, 5*time.Second)
	// Override urls generator for test
	urls := getCandidateURLsWithDefaults(dailyServer.URL, "/test", prodServer.URL, dailyServer.URL)
	if len(urls) != 2 || urls[0] != dailyServer.URL+"/test" || urls[1] != prodServer.URL+"/test" {
		t.Fatalf("unexpected candidate urls: %v", urls)
	}

	// Also verify full post fallback execution from daily to prod
	client := NewClient("mock-token", config.FallbackCodeAssistBaseURL, 5*time.Second)
	client.HTTPClient.Transport = roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if strings.Contains(req.URL.Host, "daily-cloudcode-pa") {
			dailyHits++
			return &http.Response{
				StatusCode: http.StatusServiceUnavailable,
				Body:       io.NopCloser(strings.NewReader("daily down")),
				Header:     make(http.Header),
			}, nil
		}
		prodHits++
		return &http.Response{
			StatusCode: http.StatusOK,
			Body:       io.NopCloser(strings.NewReader(`{"source": "prod"}`)),
			Header:     make(http.Header),
		}, nil
	})

	resp, err := client.post("/test", map[string]any{})
	if err != nil {
		t.Fatalf("expected post fallback to succeed, got error: %v", err)
	}
	if dailyHits != 1 || prodHits != 1 {
		t.Fatalf("expected 1 daily hit and 1 prod hit, got daily=%d, prod=%d", dailyHits, prodHits)
	}
	if src, _ := resp["source"].(string); src != "prod" {
		t.Fatalf("expected source=prod, got %v", resp["source"])
	}
}

func TestQuotaClientSymmetricFallback_FromProdToDaily(t *testing.T) {
	dailyHits := 0
	prodHits := 0

	prodURL := "https://prod.example.com"
	dailyURL := "https://daily.example.com"
	urls := getCandidateURLsWithDefaults(prodURL, "/test", prodURL, dailyURL)
	if len(urls) != 2 || urls[0] != prodURL+"/test" || urls[1] != dailyURL+"/test" {
		t.Fatalf("unexpected candidate urls: %v", urls)
	}

	// Verify full post fallback execution from prod to daily
	client := NewClient("mock-token", config.CodeAssistBaseURL, 5*time.Second)
	client.HTTPClient.Transport = roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if strings.Contains(req.URL.Host, "cloudcode-pa") && !strings.Contains(req.URL.Host, "daily") {
			prodHits++
			return &http.Response{
				StatusCode: http.StatusServiceUnavailable,
				Body:       io.NopCloser(strings.NewReader("prod down")),
				Header:     make(http.Header),
			}, nil
		}
		dailyHits++
		return &http.Response{
			StatusCode: http.StatusOK,
			Body:       io.NopCloser(strings.NewReader(`{"source": "daily"}`)),
			Header:     make(http.Header),
		}, nil
	})

	resp, err := client.post("/test", map[string]any{})
	if err != nil {
		t.Fatalf("expected post fallback to succeed, got error: %v", err)
	}
	if prodHits != 1 || dailyHits != 1 {
		t.Fatalf("expected 1 prod hit and 1 daily hit, got prod=%d, daily=%d", prodHits, dailyHits)
	}
	if src, _ := resp["source"].(string); src != "daily" {
		t.Fatalf("expected source=daily, got %v", resp["source"])
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
	if fallbackHits != 0 {
		t.Fatalf("expected fallback to not be hit, got %d", fallbackHits)
	}
}

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
