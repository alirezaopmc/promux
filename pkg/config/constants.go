package config

import (
	"os"
	"regexp"
	"strconv"
	"time"
)

const (
	DefaultPollSeconds                      = 1.0
	DefaultCooldownMinutes                  = 60
	DefaultLockTimeout                      = 10 * time.Second
	DefaultHTTPTimeout                      = 5 * time.Second
	DefaultTokenExpiryBufferSeconds         = 60
	DefaultProactiveRefreshIntervalSeconds  = 900
	DefaultQuotaCacheTTLSeconds             = 300
	UserAgent                               = "antigravity"

	CodeAssistBaseURL         = "https://cloudcode-pa.googleapis.com"
	FallbackCodeAssistBaseURL = "https://daily-cloudcode-pa.googleapis.com"
	LoadEndpoint              = "/v1internal:loadCodeAssist"
	QuotaEndpoint             = "/v1internal:retrieveUserQuotaSummary"
	UserQuotaEndpoint         = "/v1internal:retrieveUserQuota"
	UserInfoURL               = "https://www.googleapis.com/oauth2/v3/userinfo"

	DefaultOAuthClientID     = "REDACTED_OAUTH_CLIENT_ID"
	DefaultOAuthClientSecret = "REDACTED_OAUTH_CLIENT_SECRET"
)

var (
	IndividualQuotaRegex = regexp.MustCompile(`(?i)Individual quota reached`)
	ResourceExhaustedRegex = regexp.MustCompile(`(?i)RESOURCE_EXHAUSTED\s*\(\s*code\s*429\s*\)|RESOURCE_EXHAUSTED`)
	WeeklyQuotaRegex = regexp.MustCompile(`(?i)weekly quota reached`)
	ResetHintRegex = regexp.MustCompile(`(?i)Resets in\s+(?P<reset>~?[^.)\n]+)`)
)

func OAuthTokenURL() string {
	if u := os.Getenv("PROMUX_OAUTH_TOKEN_URL"); u != "" {
		return u
	}
	return "https://oauth2.googleapis.com/token"
}

func OAuthClientID() string {
	if id := os.Getenv("PROMUX_OAUTH_CLIENT_ID"); id != "" {
		return id
	}
	return DefaultOAuthClientID
}

func OAuthClientSecret() string {
	if s := os.Getenv("PROMUX_OAUTH_CLIENT_SECRET"); s != "" {
		return s
	}
	return DefaultOAuthClientSecret
}

func GetHTTPTimeout() time.Duration {
	if envVal := os.Getenv("PROMUX_HTTP_TIMEOUT"); envVal != "" {
		if sec, err := strconv.ParseFloat(envVal, 64); err == nil && sec > 0 {
			return time.Duration(sec * float64(time.Second))
		}
	}
	return DefaultHTTPTimeout
}
