package quota

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
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

type PrimeResult struct {
	Account  string  `json:"account"`
	Primed   bool    `json:"primed"`
	Reason   string  `json:"reason"`
	NewReset *string `json:"new_reset,omitempty"`
	Error    string  `json:"error,omitempty"`
}

var runAgyPrime = func(ctx context.Context) error {
	agyPath, err := exec.LookPath("agy")
	if err != nil {
		return fmt.Errorf("agy CLI not found in PATH: %w", err)
	}

	cmd := exec.CommandContext(ctx, agyPath, "-p", "ping", "--disable-slash-commands")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("agy command failed: %w (output: %s)", err, strings.TrimSpace(string(out)))
	}
	return nil
}

var queryQuota = QueryAccountQuota

// QueryAccountQuota queries Google Code Assist API live for the account's quota summary.
func QueryAccountQuota(ctx context.Context, store *storage.StorageEngine, accountName string) (*models.QuotaSummary, error) {
	acct, err := store.GetProfile(accountName)
	if err != nil {
		return nil, err
	}

	vaultToken := filepath.Join(store.AccountsDir, accountName, "antigravity-oauth-token")
	tokenData, err := auth.ReadTokenData(vaultToken)
	if err != nil {
		return nil, fmt.Errorf("failed to read token for %s: %w", accountName, err)
	}

	accToken := auth.ExtractAccessToken(tokenData)
	if accToken == "" || auth.IsTokenExpired(tokenData, config.DefaultTokenExpiryBufferSeconds) {
		_, err := auth.RefreshAccountToken(store, accountName, true)
		if err != nil {
			return nil, fmt.Errorf("failed to refresh token: %w", err)
		}
		tokenData, _ = auth.ReadTokenData(vaultToken)
		accToken = auth.ExtractAccessToken(tokenData)
	}

	client := NewClient(accToken, "", config.GetHTTPTimeout())
	projectID := ""
	if acct.ProjectID != nil {
		projectID = *acct.ProjectID
	}

	if projectID == "" {
		meta, err := client.LoadMetadata()
		if err == nil && meta.ProjectID != "" {
			projectID = meta.ProjectID
		}
	}

	return client.GetQuota(projectID)
}

// IsEligibleForPrime checks whether an account is eligible for weekly quota priming.
func IsEligibleForPrime(qs *models.QuotaSummary, now time.Time, force bool) (bool, string) {
	if force {
		return true, "forced"
	}
	if qs == nil {
		return false, "no quota data"
	}

	checkReset := func(resetStr *string) (hasActive bool, isExpired bool, duration string) {
		if resetStr != nil && *resetStr != "" {
			if resetTime, ok := ParseResetTimestamp(*resetStr); ok {
				if resetTime.After(now) {
					return true, false, FormatResetDuration(resetTime, now)
				}
				return false, true, ""
			}
		}
		return false, false, ""
	}

	gemActive, gemExpired, gemDur := checkReset(qs.GeminiWeeklyReset)
	tpActive, tpExpired, tpDur := checkReset(qs.ThirdPartyWeeklyReset)

	if gemActive {
		return false, fmt.Sprintf("active countdown (%s left)", gemDur)
	}
	if tpActive {
		return false, fmt.Sprintf("active countdown (%s left)", tpDur)
	}

	if gemExpired || tpExpired {
		return true, "weekly reset reached"
	}

	if qs.GeminiWeeklyRemaining >= 1.0 {
		return true, "weekly reset floating at 100%"
	}

	return false, "no weekly reset information"
}

// PrimeAccount safely executes a minimal headless prompt turn to fix the 7-day weekly reset countdown.
func PrimeAccount(ctx context.Context, store *storage.StorageEngine, cache *QuotaCache, accountName string, force bool) (*PrimeResult, error) {
	if store == nil {
		return nil, errors.New("storage engine cannot be nil")
	}

	state, err := store.LoadState()
	if err != nil {
		return nil, fmt.Errorf("failed to load state: %w", err)
	}

	_, ok := state.Accounts[accountName]
	if !ok {
		return nil, fmt.Errorf("%w: %s", storage.ErrAccountNotFound, accountName)
	}

	vaultToken := filepath.Join(store.AccountsDir, accountName, "antigravity-oauth-token")
	if _, err := os.Stat(vaultToken); os.IsNotExist(err) {
		return nil, fmt.Errorf("%w at %s", storage.ErrSrcTokenNotFound, vaultToken)
	}

	var qs *models.QuotaSummary
	if cache != nil {
		qs, _ = cache.Get(accountName)
	}
	if qs == nil && !force {
		var err error
		qs, err = queryQuota(ctx, store, accountName)
		if err != nil {
			return &PrimeResult{
				Account: accountName,
				Primed:  false,
				Error:   err.Error(),
			}, err
		}
		if cache != nil && qs != nil {
			_ = cache.Set(accountName, qs)
		}
	}

	eligible, reason := IsEligibleForPrime(qs, time.Now().UTC(), force)
	if !eligible {
		return &PrimeResult{
			Account: accountName,
			Primed:  false,
			Reason:  reason,
		}, nil
	}

	result := &PrimeResult{
		Account: accountName,
		Primed:  false,
		Reason:  reason,
	}

	// Proactively check and refresh token before acquiring store.LockFile.
	// This avoids deadlocks if auth.RefreshAccountToken needs to perform Tier 2 fallback
	// which acquires store.LockFile internally.
	tokenData, err := auth.ReadTokenData(vaultToken)
	if err != nil {
		return nil, fmt.Errorf("failed to read vault token: %w", err)
	}
	if auth.IsTokenExpired(tokenData, config.DefaultTokenExpiryBufferSeconds) {
		if _, err := auth.RefreshAccountToken(store, accountName, true); err != nil {
			return nil, fmt.Errorf("failed to refresh token for %s: %w", accountName, err)
		}
	}

	primeErr := lock.WithLock(ctx, store.LockFile, 30*time.Second, func() error {
		curState, err := store.LoadState()
		if err != nil {
			return err
		}

		isActive := curState.Active != nil && *curState.Active == accountName

		if isActive {
			auth.SafeSyncLiveToVault(store, accountName)
		}

		backupPath := store.LiveToken + fmt.Sprintf(".prime_bak_%d_%d", os.Getpid(), time.Now().UnixNano())
		liveExisted := false

		if !isActive {
			if err := os.MkdirAll(filepath.Dir(store.LiveToken), 0755); err != nil {
				return fmt.Errorf("failed to create live token directory: %w", err)
			}
			if _, err := os.Stat(store.LiveToken); err == nil {
				liveExisted = true
				if err := auth.AtomicCopyToken(store.LiveToken, backupPath); err != nil {
					return fmt.Errorf("failed to backup live token: %w", err)
				}
			}

			defer func() {
				if liveExisted {
					_ = os.Rename(backupPath, store.LiveToken)
				} else {
					_ = os.Remove(store.LiveToken)
					_ = os.Remove(backupPath)
				}
			}()

			// Copy vault token into live token path
			if err := auth.AtomicCopyToken(vaultToken, store.LiveToken); err != nil {
				return fmt.Errorf("failed to stage vault token to live path: %w", err)
			}
		}

		// Double-check live token expiry inside lock via native refresh (no store.LockFile re-entry)
		liveTokenData, err := auth.ReadTokenData(store.LiveToken)
		if err == nil && auth.IsTokenExpired(liveTokenData, config.DefaultTokenExpiryBufferSeconds) {
			if _, _, err := auth.RefreshTokenNative(store.LiveToken, liveTokenData); err != nil {
				return fmt.Errorf("failed to refresh live token: %w", err)
			}
		}

		// Execute child command with 20-second timeout context
		agyCtx, agyCancel := context.WithTimeout(ctx, 20*time.Second)
		defer agyCancel()

		if err := runAgyPrime(agyCtx); err != nil {
			return fmt.Errorf("headless priming turn failed: %w", err)
		}

		// Copy updated live token back to account vault
		if err := auth.AtomicCopyToken(store.LiveToken, vaultToken); err != nil {
			return fmt.Errorf("failed to sync renewed token to vault: %w", err)
		}

		return nil
	})

	if primeErr != nil {
		result.Error = primeErr.Error()
		return result, primeErr
	}

	result.Primed = true

	// Live Quota Verification & cache update
	newQs, err := queryQuota(ctx, store, accountName)
	if err != nil {
		result.Error = fmt.Sprintf("primed successfully, but failed to refresh quota: %v", err)
	} else if newQs != nil {
		result.NewReset = newQs.GeminiWeeklyReset
		if cache != nil {
			_ = cache.Set(accountName, newQs)
		}
	}

	return result, nil
}

// PrimeAllEligible iterates over all accounts in the vault and primes those eligible.
func PrimeAllEligible(ctx context.Context, store *storage.StorageEngine, cache *QuotaCache, force bool) ([]PrimeResult, error) {
	if store == nil {
		return nil, errors.New("storage engine cannot be nil")
	}

	state, err := store.LoadState()
	if err != nil {
		return nil, fmt.Errorf("failed to load state: %w", err)
	}

	if len(state.Accounts) == 0 {
		return []PrimeResult{}, nil
	}

	names := make([]string, 0, len(state.Accounts))
	for name := range state.Accounts {
		names = append(names, name)
	}
	sort.Strings(names)

	results := make([]PrimeResult, 0, len(names))
	for _, name := range names {
		res, err := PrimeAccount(ctx, store, cache, name, force)
		if res != nil {
			results = append(results, *res)
		} else if err != nil {
			results = append(results, PrimeResult{
				Account: name,
				Primed:  false,
				Error:   err.Error(),
			})
		}
	}

	return results, nil
}
