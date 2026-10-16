package failover

import (
	"context"
	"fmt"
	"sort"
	"time"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/storage"
)

type FailoverEngine struct {
	Storage *storage.StorageEngine
}

func New(storage *storage.StorageEngine) *FailoverEngine {
	return &FailoverEngine{Storage: storage}
}

func (f *FailoverEngine) GetEligibleStandby(exclude string) ([]*models.AccountMeta, error) {
	state, err := f.Storage.LoadState()
	if err != nil {
		return nil, err
	}

	now := time.Now().UTC()
	var eligible []*models.AccountMeta

	for name, acct := range state.Accounts {
		if name == exclude {
			continue
		}
		if acct.State(now) == models.StateStandby {
			eligible = append(eligible, acct)
		}
	}

	// Sort LRU: least recently used first (nil LastUsedAt first)
	sort.Slice(eligible, func(i, j int) bool {
		ti := eligible[i].LastUsedAt
		tj := eligible[j].LastUsedAt
		if ti == nil && tj != nil {
			return true
		}
		if ti != nil && tj == nil {
			return false
		}
		if ti == nil && tj == nil {
			return eligible[i].Name < eligible[j].Name
		}
		return ti.Before(*tj)
	})

	return eligible, nil
}

func (f *FailoverEngine) ApplyCooldown(name string, minutes int) (*time.Time, error) {
	if minutes <= 0 {
		minutes = config.DefaultCooldownMinutes
	}
	until := time.Now().UTC().Add(time.Duration(minutes) * time.Minute)

	err := f.Storage.Transaction(context.Background(), func(state *models.State) error {
		if acct, ok := state.Accounts[name]; ok {
			acct.CooldownUntil = &until
		}
		return nil
	})
	return &until, err
}

func (f *FailoverEngine) RotateNext(reason string, cooldownMinutes int) (*models.RotationResult, error) {
	if cooldownMinutes <= 0 {
		cooldownMinutes = config.DefaultCooldownMinutes
	}

	var res *models.RotationResult

	err := f.Storage.TransactionWithContext(context.Background(), func(txCtx context.Context, state *models.State) error {
		var activeName string
		if state.Active != nil {
			activeName = *state.Active
		}

		var cdUntil *time.Time
		if activeName != "" {
			until := time.Now().UTC().Add(time.Duration(cooldownMinutes) * time.Minute)
			cdUntil = &until
			if acct, ok := state.Accounts[activeName]; ok {
				acct.CooldownUntil = &until
			}
		}

		now := time.Now().UTC()
		var candidates []*models.AccountMeta
		for name, acct := range state.Accounts {
			if name == activeName {
				continue
			}
			if acct.State(now) == models.StateStandby {
				candidates = append(candidates, acct)
			}
		}

		if len(candidates) == 0 {
			res = &models.RotationResult{
				Success:       false,
				FromAccount:   &activeName,
				ToAccount:     nil,
				Reason:        fmt.Sprintf("No eligible standby accounts (%s)", reason),
				CooldownUntil: cdUntil,
			}
			return nil
		}

		// Sort LRU
		sort.Slice(candidates, func(i, j int) bool {
			ti := candidates[i].LastUsedAt
			tj := candidates[j].LastUsedAt
			if ti == nil && tj != nil {
				return true
			}
			if ti != nil && tj == nil {
				return false
			}
			if ti == nil && tj == nil {
				return candidates[i].Name < candidates[j].Name
			}
			return ti.Before(*tj)
		})

		nextTarget := candidates[0].Name
		if err := f.Storage.SwitchProfileWithContext(txCtx, nextTarget); err != nil {
			return fmt.Errorf("failed to switch profile to %s: %w", nextTarget, err)
		}

		res = &models.RotationResult{
			Success:       true,
			FromAccount:   &activeName,
			ToAccount:     &nextTarget,
			Reason:        reason,
			CooldownUntil: cdUntil,
		}
		return nil
	})

	return res, err
}
