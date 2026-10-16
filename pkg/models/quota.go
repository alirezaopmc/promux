package models

import "math"

type QuotaBucket struct {
	BucketID          string  `json:"bucket_id"`
	DisplayName       string  `json:"display_name"`
	Window            string  `json:"window"`
	RemainingFraction float64 `json:"remaining_fraction"`
	ResetTime         *string `json:"reset_time"`
}

type QuotaSummary struct {
	Gemini5hRemaining          float64 `json:"gemini_5h_remaining"`
	GeminiWeeklyRemaining      float64 `json:"gemini_weekly_remaining"`
	ThirdParty5hRemaining      float64 `json:"third_party_5h_remaining"`
	ThirdPartyWeeklyRemaining  float64 `json:"third_party_weekly_remaining"`
	Gemini5hReset              *string `json:"gemini_5h_reset"`
	GeminiWeeklyReset          *string `json:"gemini_weekly_reset"`
	ThirdParty5hReset          *string `json:"third_party_5h_reset"`
	ThirdPartyWeeklyReset      *string `json:"third_party_weekly_reset"`
}

func NewQuotaSummary() *QuotaSummary {
	return &QuotaSummary{
		Gemini5hRemaining:         1.0,
		GeminiWeeklyRemaining:     1.0,
		ThirdParty5hRemaining:     1.0,
		ThirdPartyWeeklyRemaining: 1.0,
	}
}

func (q *QuotaSummary) MinShortWindow() float64 {
	return math.Min(q.Gemini5hRemaining, q.ThirdParty5hRemaining)
}

func (q *QuotaSummary) MinWeekly() float64 {
	return math.Min(q.GeminiWeeklyRemaining, q.ThirdPartyWeeklyRemaining)
}

type TokenData struct {
	AccessToken  string  `json:"access_token"`
	RefreshToken *string `json:"refresh_token"`
	Expiry       *string `json:"expiry"`
	ExpiresAt    *int64  `json:"expires_at"`
	TokenType    string  `json:"token_type"`
}
