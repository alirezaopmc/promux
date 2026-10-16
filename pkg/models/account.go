package models

import (
	"encoding/json"
	"time"
)

type AccountState string

const (
	StateStandby  AccountState = "standby"
	StateActive   AccountState = "active"
	StateCooldown AccountState = "cooldown"
	StateDisabled AccountState = "disabled"
)

type AccountMeta struct {
	Name          string     `json:"name"`
	Enabled       bool       `json:"enabled"`
	CooldownUntil *time.Time `json:"cooldown_until"`
	SavedAt       *time.Time `json:"saved_at"`
	LastUsedAt    *time.Time `json:"last_used_at"`
	Email         *string    `json:"email"`
	ProjectID     *string    `json:"project_id"`
	PlanType      string     `json:"plan_type"`
}

func (a *AccountMeta) State(now time.Time) AccountState {
	if !a.Enabled {
		return StateDisabled
	}
	if a.CooldownUntil != nil && a.CooldownUntil.After(now) {
		return StateCooldown
	}
	return StateStandby
}

type State struct {
	Active   *string                 `json:"active"`
	Accounts map[string]*AccountMeta `json:"accounts"`
}

func NewState() *State {
	return &State{
		Active:   nil,
		Accounts: make(map[string]*AccountMeta),
	}
}

type RotationResult struct {
	Success       bool       `json:"success"`
	FromAccount   *string    `json:"from_account"`
	ToAccount     *string    `json:"to_account"`
	Reason        string     `json:"reason"`
	CooldownUntil *time.Time `json:"cooldown_until"`
}

type SmartRotationResult struct {
	RotationResult
	Model              string   `json:"model"`
	FiveHourRemaining  *float64 `json:"five_hour_remaining"`
	WeeklyRemaining    *float64 `json:"weekly_remaining"`
}

// UnmarshalJSON custom unmarshaler for AccountMeta to handle various ISO datetime formats
func (a *AccountMeta) UnmarshalJSON(data []byte) error {
	type Alias AccountMeta
	aux := &struct {
		CooldownUntil *string `json:"cooldown_until"`
		SavedAt       *string `json:"saved_at"`
		LastUsedAt    *string `json:"last_used_at"`
		*Alias
	}{
		Alias: (*Alias)(a),
	}
	if err := json.Unmarshal(data, aux); err != nil {
		return err
	}
	if a.PlanType == "" {
		a.PlanType = "STANDARD"
	}
	parseTime := func(s *string) *time.Time {
		if s == nil || *s == "" {
			return nil
		}
		for _, layout := range []string{
			time.RFC3339Nano,
			time.RFC3339,
			"2006-01-02T15:04:05.999999",
			"2006-01-02T15:04:05",
		} {
			if t, err := time.Parse(layout, *s); err == nil {
				utc := t.UTC()
				return &utc
			}
		}
		return nil
	}
	a.CooldownUntil = parseTime(aux.CooldownUntil)
	a.SavedAt = parseTime(aux.SavedAt)
	a.LastUsedAt = parseTime(aux.LastUsedAt)
	return nil
}
