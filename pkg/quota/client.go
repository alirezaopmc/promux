package quota

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/models"
)

type QuotaClient struct {
	Token      string
	BaseURL    string
	Timeout    time.Duration
	HTTPClient *http.Client
}

func NewClient(token string, baseURL string, timeout time.Duration) *QuotaClient {
	if baseURL == "" {
		baseURL = config.DetectCodeAssistURL("")
	}
	baseURL = strings.TrimRight(baseURL, "/")
	if timeout <= 0 {
		timeout = config.GetHTTPTimeout()
	}

	return &QuotaClient{
		Token:      token,
		BaseURL:    baseURL,
		Timeout:    timeout,
		HTTPClient: &http.Client{Timeout: timeout},
	}
}

func (c *QuotaClient) post(endpoint string, payload any) (map[string]any, error) {
	bodyBytes, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	urlsToTry := []string{c.BaseURL + endpoint}
	if c.BaseURL != config.FallbackCodeAssistBaseURL {
		urlsToTry = append(urlsToTry, config.FallbackCodeAssistBaseURL+endpoint)
	}

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

func (c *QuotaClient) FetchEmail() (string, error) {
	req, err := http.NewRequest(http.MethodGet, config.UserInfoURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	req.Header.Set("User-Agent", config.UserAgent)

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("userinfo endpoint returned HTTP %d", resp.StatusCode)
	}

	var data map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return "", err
	}

	if email, ok := data["email"].(string); ok {
		return email, nil
	}
	return "", nil
}

type MetadataResponse struct {
	ProjectID string
	Tier      string
	PlanName  string
}

func (c *QuotaClient) LoadMetadata() (*MetadataResponse, error) {
	payload := map[string]any{
		"metadata": map[string]any{
			"ideType":    "ANTIGRAVITY",
			"platform":   "PLATFORM_UNSPECIFIED",
			"pluginType": "GEMINI",
		},
	}

	resp, err := c.post(config.LoadEndpoint, payload)
	if err != nil {
		return nil, err
	}

	res := &MetadataResponse{
		Tier:     "free-tier",
		PlanName: "Antigravity",
	}

	if proj, ok := resp["cloudaicompanionProject"].(map[string]any); ok {
		if id, ok := proj["id"].(string); ok {
			res.ProjectID = id
		}
	} else if projStr, ok := resp["cloudaicompanionProject"].(string); ok {
		res.ProjectID = projStr
	}

	if tierInfo, ok := resp["currentTier"].(map[string]any); ok {
		if id, ok := tierInfo["id"].(string); ok {
			res.Tier = id
		}
		if name, ok := tierInfo["name"].(string); ok {
			res.PlanName = name
		}
	}

	return res, nil
}

func (c *QuotaClient) GetQuota(projectID string) (*models.QuotaSummary, error) {
	resp, err := c.post(config.QuotaEndpoint, map[string]any{"project": projectID})
	if err != nil {
		return nil, err
	}

	qs := models.NewQuotaSummary()

	groups, _ := resp["groups"].([]any)
	for _, g := range groups {
		groupMap, ok := g.(map[string]any)
		if !ok {
			continue
		}
		displayName, _ := groupMap["displayName"].(string)
		isGemini := strings.Contains(strings.ToLower(displayName), "gemini")

		buckets, _ := groupMap["buckets"].([]any)
		for _, b := range buckets {
			bucketMap, ok := b.(map[string]any)
			if !ok {
				continue
			}

			window, _ := bucketMap["window"].(string)
			windowLower := strings.ToLower(window)

			var remVal float64 = 1.0
			if rem, exists := bucketMap["remainingFraction"]; exists {
				switch v := rem.(type) {
				case float64:
					remVal = v
				case int:
					remVal = float64(v)
				case int64:
					remVal = float64(v)
				case json.Number:
					if f, err := v.Float64(); err == nil {
						remVal = f
					}
				}
			}

			var resetStr *string
			if reset, ok := bucketMap["resetTime"].(string); ok && reset != "" {
				resetStr = &reset
			}

			isShort := windowLower == "5h" || strings.Contains(windowLower, "short")
			isWeekly := strings.Contains(windowLower, "weekly")

			if isGemini {
				if isShort {
					qs.Gemini5hRemaining = remVal
					qs.Gemini5hReset = resetStr
				} else if isWeekly {
					qs.GeminiWeeklyRemaining = remVal
					qs.GeminiWeeklyReset = resetStr
				}
			} else {
				if isShort {
					qs.ThirdParty5hRemaining = remVal
					qs.ThirdParty5hReset = resetStr
				} else if isWeekly {
					qs.ThirdPartyWeeklyRemaining = remVal
					qs.ThirdPartyWeeklyReset = resetStr
				}
			}
		}
	}

	// Query retrieveUserQuota for real-time per-model consumption
	uqResp, err := c.post(config.UserQuotaEndpoint, map[string]any{"project": projectID})
	if err == nil && uqResp != nil {
		if buckets, ok := uqResp["buckets"].([]any); ok {
			now := time.Now().UTC()
			for _, b := range buckets {
				bMap, ok := b.(map[string]any)
				if !ok {
					continue
				}
				modelID, _ := bMap["modelId"].(string)
				mid := strings.ToLower(modelID)

				var remVal *float64
				if rem, exists := bMap["remainingFraction"]; exists {
					switch v := rem.(type) {
					case float64:
						remVal = &v
					case int:
						fv := float64(v)
						remVal = &fv
					case int64:
						fv := float64(v)
						remVal = &fv
					case json.Number:
						if f, err := v.Float64(); err == nil {
							remVal = &f
						}
					}
				}
				if remVal == nil {
					continue
				}

				var resetStr *string
				var isShort bool = true
				if rst, ok := bMap["resetTime"].(string); ok && rst != "" {
					resetStr = &rst
					if t, ok := ParseResetTimestamp(rst); ok {
						diff := t.Sub(now)
						isShort = diff <= 6*time.Hour
					}
				}

				if strings.Contains(mid, "gemini") {
					var diff time.Duration
					hasDiff := false
					if resetStr != nil {
						if t, ok := ParseResetTimestamp(*resetStr); ok {
							diff = t.Sub(now)
							hasDiff = true
						}
					}

					// Resets within 24 hours reflect the 5-hour rolling limit or daily exhaustion cooldown
					if !hasDiff || diff <= 24*time.Hour {
						if *remVal < qs.Gemini5hRemaining || qs.Gemini5hReset == nil {
							qs.Gemini5hRemaining = *remVal
							if resetStr != nil {
								qs.Gemini5hReset = resetStr
							}
						}
					} else {
						// Multi-day reset (> 24 hours) indicates weekly window
						if *remVal < qs.GeminiWeeklyRemaining || qs.GeminiWeeklyReset == nil {
							qs.GeminiWeeklyRemaining = *remVal
							if resetStr != nil {
								qs.GeminiWeeklyReset = resetStr
							}
						}
						if *remVal < qs.Gemini5hRemaining {
							qs.Gemini5hRemaining = *remVal
						}
					}
				} else if strings.Contains(mid, "claude") || strings.Contains(mid, "gpt") {
					if isShort {
						if *remVal < qs.ThirdParty5hRemaining || qs.ThirdParty5hReset == nil {
							qs.ThirdParty5hRemaining = *remVal
							if resetStr != nil {
								qs.ThirdParty5hReset = resetStr
							}
						}
					} else {
						if *remVal < qs.ThirdPartyWeeklyRemaining || qs.ThirdPartyWeeklyReset == nil {
							qs.ThirdPartyWeeklyRemaining = *remVal
							if resetStr != nil {
								qs.ThirdPartyWeeklyReset = resetStr
							}
						}
					}
				}
			}
		}
	}

	return qs, nil
}
