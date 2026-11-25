package hub

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alirezaopmc/promux/pkg/auth"
	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/lock"
	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/storage"
)

// SwitchResult reports the outcome of switching the Hub active profile.
type SwitchResult struct {
	Profile      string         `json:"profile"`
	HubTokenPath string         `json:"hub_token_path"`
	WriterID     string         `json:"writer_id"`
	Refreshed    bool           `json:"refreshed"`
	Restart      *RestartResult `json:"restart,omitempty"`
}

// RotateResult reports the outcome of rotating the Hub active profile.
type RotateResult struct {
	Success       bool           `json:"success"`
	FromAccount   *string        `json:"from_account"`
	ToAccount     *string        `json:"to_account"`
	Reason        string         `json:"reason"`
	CooldownUntil *time.Time     `json:"cooldown_until"`
	Refreshed     bool           `json:"refreshed"`
	Restart       *RestartResult `json:"restart,omitempty"`
}

// StatusResult reports the current Hub active profile, live token sync status, and running daemons.
type StatusResult struct {
	HubActive    *string           `json:"hub_active"`
	Email        string            `json:"email,omitempty"`
	PlanType     string            `json:"plan_type,omitempty"`
	Sync         *auth.HubSyncInfo `json:"sync"`
	Daemons      []DaemonInfo      `json:"daemons"`
	HubTokenPath string            `json:"hub_token_path"`
}

func validateProfileName(name string) error {
	if name == "" || name == "." || name == ".." || strings.ContainsAny(name, "/\\") {
		return fmt.Errorf("invalid profile name %q", name)
	}
	return nil
}

// EnsureVaultTokenFresh checks the vaulted OAuth token for name and refreshes it if expired
// or missing an access token. Callers must NOT hold store.LockFile when calling this helper.
func EnsureVaultTokenFresh(store *storage.StorageEngine, name string) (string, bool, error) {
	if store == nil {
		return "", false, errors.New("storage engine is required")
	}
	if _, err := store.GetProfile(name); err != nil {
		return "", false, err
	}

	vaultToken := filepath.Join(store.AccountsDir, name, "antigravity-oauth-token")
	tokenData, err := auth.ReadTokenData(vaultToken)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", false, fmt.Errorf("%w at %s", storage.ErrSrcTokenNotFound, vaultToken)
		}
		return "", false, fmt.Errorf("failed to read vault token for %s: %w", name, err)
	}

	if auth.ExtractAccessToken(tokenData) != "" && !auth.IsTokenExpired(tokenData, config.DefaultTokenExpiryBufferSeconds) {
		return vaultToken, false, nil
	}

	refreshed, err := auth.RefreshAccountToken(store, name, true)
	if err != nil {
		return "", false, fmt.Errorf("failed to refresh token for %s: %w", name, err)
	}
	return vaultToken, refreshed, nil
}

func sortStandbyLRU(candidates []*models.AccountMeta) {
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
		if ti.Equal(*tj) {
			return candidates[i].Name < candidates[j].Name
		}
		return ti.Before(*tj)
	})
}

func maybeRestartDaemons(ctx context.Context, opts RestartOptions) (*RestartResult, error) {
	if opts.NoRestart {
		return nil, nil
	}
	return RestartDaemons(ctx, config.HubDaemonDir(""), opts)
}

func applyHubSwitch(state *models.State, name, vaultToken, hubTokenPath string, now time.Time) (string, error) {
	if _, ok := state.Accounts[name]; !ok {
		return "", fmt.Errorf("%w: %s", storage.ErrAccountNotFound, name)
	}
	wid, err := auth.SyncVaultToHubToken(vaultToken, hubTokenPath)
	if err != nil {
		return "", err
	}
	acct := state.Accounts[name]
	acct.LastUsedAt = &now
	target := name
	state.HubActive = &target
	return wid, nil
}

// Switch sets the Hub active profile to name, refreshing expired vaulted tokens beforehand,
// atomically syncing the Hub live token while preserving writer_id, and restarting Hub
// language_server daemons unless opts.NoRestart is true.
func Switch(ctx context.Context, store *storage.StorageEngine, name string, opts RestartOptions) (*SwitchResult, error) {
	if store == nil {
		return nil, errors.New("storage engine is required")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}

	// Validate and refresh before acquiring manager.lock or touching Hub live token/state.
	vaultToken, refreshed, err := EnsureVaultTokenFresh(store, name)
	if err != nil {
		return nil, err
	}

	hubTokenPath := config.HubLiveTokenFile("")
	var writerID string

	err = store.Transaction(ctx, func(state *models.State) error {
		wid, txErr := applyHubSwitch(state, name, vaultToken, hubTokenPath, time.Now().UTC())
		if txErr != nil {
			return txErr
		}
		writerID = wid
		return nil
	})
	if err != nil {
		return nil, err
	}

	res := &SwitchResult{
		Profile:      name,
		HubTokenPath: hubTokenPath,
		WriterID:     writerID,
		Refreshed:    refreshed,
	}

	restartRes, restartErr := maybeRestartDaemons(ctx, opts)
	res.Restart = restartRes
	if restartErr != nil {
		return res, fmt.Errorf("switched hub profile to %s, but failed to restart hub daemon: %w", name, restartErr)
	}
	return res, nil
}

// RotateNext rotates the Hub active profile to the least-recently-used standby account,
// placing the previous Hub active profile on cooldown for cooldownMinutes (defaulting to
// config.DefaultCooldownMinutes when <= 0), refreshing the target token if expired, and
// restarting Hub language_server daemons unless opts.NoRestart is true.
func RotateNext(ctx context.Context, store *storage.StorageEngine, reason string, cooldownMinutes int, opts RestartOptions) (*RotateResult, error) {
	if store == nil {
		return nil, errors.New("storage engine is required")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if cooldownMinutes <= 0 {
		cooldownMinutes = config.DefaultCooldownMinutes
	}

	var fromName string
	var target string

	if err := lock.WithLock(ctx, store.LockFile, config.DefaultLockTimeout, func() error {
		state, err := store.LoadState()
		if err != nil {
			return err
		}
		if state.HubActive != nil {
			fromName = *state.HubActive
		}

		now := time.Now().UTC()
		var candidates []*models.AccountMeta
		for name, acct := range state.Accounts {
			if name == fromName {
				continue
			}
			if acct.State(now) == models.StateStandby {
				candidates = append(candidates, acct)
			}
		}
		if len(candidates) == 0 {
			return nil
		}

		sortStandbyLRU(candidates)
		target = candidates[0].Name
		return nil
	}); err != nil {
		return nil, err
	}

	if target == "" {
		var fromPtr *string
		if fromName != "" {
			prev := fromName
			fromPtr = &prev
		}
		noCandidateReason := "No eligible standby accounts"
		if reason != "" {
			noCandidateReason = fmt.Sprintf("No eligible standby accounts (%s)", reason)
		}
		return &RotateResult{
			Success:     false,
			FromAccount: fromPtr,
			ToAccount:   nil,
			Reason:      noCandidateReason,
		}, nil
	}

	// Ensure candidate token freshness outside manager.lock before mutating state or Hub token.
	vaultToken, refreshed, err := EnsureVaultTokenFresh(store, target)
	if err != nil {
		return nil, err
	}

	hubTokenPath := config.HubLiveTokenFile("")
	var fromPtr *string
	var cdUntil *time.Time

	err = store.Transaction(ctx, func(state *models.State) error {
		acct, ok := state.Accounts[target]
		if !ok {
			return fmt.Errorf("%w: %s", storage.ErrAccountNotFound, target)
		}
		now := time.Now().UTC()
		if st := acct.State(now); st != models.StateStandby {
			return fmt.Errorf("candidate profile %s is no longer standby (%s)", target, st)
		}

		var prevActive string
		if state.HubActive != nil {
			prevActive = *state.HubActive
		}

		if _, txErr := applyHubSwitch(state, target, vaultToken, hubTokenPath, now); txErr != nil {
			return txErr
		}

		if prevActive != "" && prevActive != target {
			fromPtr = &prevActive
			until := now.Add(time.Duration(cooldownMinutes) * time.Minute)
			cdUntil = &until
			if prevAcct, ok := state.Accounts[prevActive]; ok {
				prevAcct.CooldownUntil = &until
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}

	toCopy := target
	res := &RotateResult{
		Success:       true,
		FromAccount:   fromPtr,
		ToAccount:     &toCopy,
		Reason:        reason,
		CooldownUntil: cdUntil,
		Refreshed:     refreshed,
	}

	restartRes, restartErr := maybeRestartDaemons(ctx, opts)
	res.Restart = restartRes
	if restartErr != nil {
		return res, fmt.Errorf("rotated hub profile to %s, but failed to restart hub daemon: %w", target, restartErr)
	}
	return res, nil
}

// Save imports the current Hub live OAuth token into the vault under name (stripping writer_id,
// inferring email from id_token_email when email is nil/empty) and marks name as HubActive
// without altering the CLI active profile.
func Save(store *storage.StorageEngine, name string, email, projectID *string, planType string) (*models.AccountMeta, error) {
	if store == nil {
		return nil, errors.New("storage engine is required")
	}
	if err := validateProfileName(name); err != nil {
		return nil, err
	}

	hubTokenPath := config.HubLiveTokenFile("")
	_, emailFromTok, cleanBytes, err := auth.ReadHubTokenForVault(hubTokenPath)
	if err != nil {
		return nil, err
	}

	if (email == nil || *email == "") && emailFromTok != "" {
		extracted := emailFromTok
		email = &extracted
	}

	return store.SaveProfileFromTokenBytes(name, cleanBytes, email, projectID, planType, false, true)
}

// Status returns the current Hub active profile metadata, live token sync status, and live
// Hub language_server daemons.
func Status(store *storage.StorageEngine) (*StatusResult, error) {
	if store == nil {
		return nil, errors.New("storage engine is required")
	}

	hubTokenPath := config.HubLiveTokenFile("")
	daemonDir := config.HubDaemonDir("")

	state, err := store.LoadState()
	if err != nil {
		return nil, fmt.Errorf("failed to load state: %w", err)
	}

	var hubActive *string
	var email, planType string
	if state.HubActive != nil && *state.HubActive != "" {
		activeCopy := *state.HubActive
		hubActive = &activeCopy
		if acct, ok := state.Accounts[activeCopy]; ok && acct != nil {
			if acct.Email != nil {
				email = *acct.Email
			}
			planType = acct.PlanType
		}
	}

	syncInfo, err := auth.CheckHubSyncStatus(store, hubTokenPath)
	if err != nil {
		return nil, fmt.Errorf("failed to check hub sync status: %w", err)
	}

	daemons, err := DiscoverDaemons(daemonDir)
	if err != nil {
		return nil, fmt.Errorf("failed to discover hub daemons: %w", err)
	}
	if daemons == nil {
		daemons = []DaemonInfo{}
	}

	return &StatusResult{
		HubActive:    hubActive,
		Email:        email,
		PlanType:     planType,
		Sync:         syncInfo,
		Daemons:      daemons,
		HubTokenPath: hubTokenPath,
	}, nil
}
