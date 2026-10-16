package models

import (
	"encoding/json"
	"testing"
	"time"
)

func TestAccountState(t *testing.T) {
	now := time.Now().UTC()
	future := now.Add(1 * time.Hour)
	past := now.Add(-1 * time.Hour)

	// Disabled
	acctDisabled := &AccountMeta{Name: "test", Enabled: false}
	if got := acctDisabled.State(now); got != StateDisabled {
		t.Fatalf("expected disabled, got %s", got)
	}

	// Standby
	acctStandby := &AccountMeta{Name: "test", Enabled: true}
	if got := acctStandby.State(now); got != StateStandby {
		t.Fatalf("expected standby, got %s", got)
	}

	// Cooldown active
	acctCooldown := &AccountMeta{Name: "test", Enabled: true, CooldownUntil: &future}
	if got := acctCooldown.State(now); got != StateCooldown {
		t.Fatalf("expected cooldown, got %s", got)
	}

	// Cooldown expired -> standby
	acctExpired := &AccountMeta{Name: "test", Enabled: true, CooldownUntil: &past}
	if got := acctExpired.State(now); got != StateStandby {
		t.Fatalf("expected standby, got %s", got)
	}
}

func TestJSONCompatibilityWithPythonState(t *testing.T) {
	pythonJSON := `{
		"active": "alpha",
		"accounts": {
			"alpha": {
				"name": "alpha",
				"enabled": true,
				"cooldown_until": "2026-10-16T02:00:00+00:00",
				"saved_at": "2026-10-15T12:00:00+00:00",
				"last_used_at": "2026-10-15T12:30:00+00:00",
				"email": "user@example.com",
				"project_id": "proj-123",
				"plan_type": "PRO"
			}
		}
	}`

	var s State
	if err := json.Unmarshal([]byte(pythonJSON), &s); err != nil {
		t.Fatalf("failed to unmarshal state json: %v", err)
	}

	if s.Active == nil || *s.Active != "alpha" {
		t.Fatalf("expected active 'alpha', got %v", s.Active)
	}
	alpha, ok := s.Accounts["alpha"]
	if !ok {
		t.Fatalf("expected account 'alpha'")
	}
	if alpha.Name != "alpha" || !alpha.Enabled || alpha.PlanType != "PRO" {
		t.Fatalf("unexpected account details: %+v", alpha)
	}
	if alpha.Email == nil || *alpha.Email != "user@example.com" {
		t.Fatalf("expected email user@example.com, got %v", alpha.Email)
	}
	if alpha.ProjectID == nil || *alpha.ProjectID != "proj-123" {
		t.Fatalf("expected project_id proj-123, got %v", alpha.ProjectID)
	}
	if alpha.CooldownUntil == nil {
		t.Fatalf("expected cooldown_until parsed")
	}
}

func TestQuotaSummary(t *testing.T) {
	q := NewQuotaSummary()
	q.Gemini5hRemaining = 0.8
	q.ThirdParty5hRemaining = 0.5
	q.GeminiWeeklyRemaining = 0.9
	q.ThirdPartyWeeklyRemaining = 0.95

	if q.MinShortWindow() != 0.5 {
		t.Fatalf("expected MinShortWindow 0.5, got %v", q.MinShortWindow())
	}
	if q.MinWeekly() != 0.9 {
		t.Fatalf("expected MinWeekly 0.9, got %v", q.MinWeekly())
	}
}
