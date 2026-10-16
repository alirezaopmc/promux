package auth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/lock"
	"github.com/alirezaopmc/promux/pkg/storage"
)

type TokenRefreshResponse struct {
	AccessToken  string  `json:"access_token"`
	ExpiresIn    int     `json:"expires_in"`
	RefreshToken *string `json:"refresh_token"`
	TokenType    string  `json:"token_type"`
}

var ErrTokenRevoked = errors.New("refresh token has been revoked (invalid_grant)")

func RefreshTokenNative(tokenPath string, tokenData RawTokenData) (string, bool, error) {
	refToken := ExtractRefreshToken(tokenData)
	if refToken == "" {
		return "", false, errors.New("no refresh_token found in token data")
	}

	clientID := config.OAuthClientID()
	clientSecret := config.OAuthClientSecret()

	data := url.Values{}
	data.Set("client_id", clientID)
	data.Set("client_secret", clientSecret)
	data.Set("refresh_token", refToken)
	data.Set("grant_type", "refresh_token")

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.PostForm(config.OAuthTokenURL(), data)
	if err != nil {
		return "", false, err
	}
	defer resp.Body.Close()

	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", false, err
	}

	if resp.StatusCode != http.StatusOK {
		bodyStr := string(bodyBytes)
		if strings.Contains(bodyStr, "invalid_grant") {
			return "", true, ErrTokenRevoked
		}
		return "", false, fmt.Errorf("oauth refresh failed with HTTP %d: %s", resp.StatusCode, bodyStr)
	}

	var res TokenRefreshResponse
	if err := json.Unmarshal(bodyBytes, &res); err != nil {
		return "", false, err
	}

	if res.AccessToken == "" {
		return "", false, errors.New("empty access_token returned from oauth refresh")
	}

	// Update tokenData map
	now := time.Now().UTC()
	expiresIn := res.ExpiresIn
	if expiresIn <= 0 {
		expiresIn = 3600
	}
	newExpiry := now.Add(time.Duration(expiresIn) * time.Second).Format(time.RFC3339)

	if tok, ok := tokenData["token"].(map[string]any); ok {
		tok["access_token"] = res.AccessToken
		tok["expiry"] = newExpiry
		if res.RefreshToken != nil && *res.RefreshToken != "" {
			tok["refresh_token"] = *res.RefreshToken
		}
	} else {
		tokenData["access_token"] = res.AccessToken
		tokenData["expiry"] = newExpiry
		if res.RefreshToken != nil && *res.RefreshToken != "" {
			tokenData["refresh_token"] = *res.RefreshToken
		}
	}

	// Write atomically back to tokenPath
	tmpPath := tokenPath + ".tmp"
	f, err := os.OpenFile(tmpPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return "", false, err
	}

	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(tokenData); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpPath)
		return "", false, err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return "", false, err
	}

	if err := os.Rename(tmpPath, tokenPath); err != nil {
		return "", false, err
	}

	return res.AccessToken, false, nil
}

func RefreshAccountToken(store *storage.StorageEngine, accountName string, force bool) (bool, error) {
	state, err := store.LoadState()
	if err != nil {
		return false, err
	}

	isActive := state.Active != nil && *state.Active == accountName
	vaultToken := filepath.Join(store.AccountsDir, accountName, "antigravity-oauth-token")

	tokenPath := vaultToken
	if isActive && store.LiveToken != "" {
		if _, err := os.Stat(store.LiveToken); err == nil {
			tokenPath = store.LiveToken
		}
	}

	data, err := ReadTokenData(tokenPath)
	if err != nil {
		return false, fmt.Errorf("failed to read token for %s: %w", accountName, err)
	}

	if !force && !IsTokenExpired(data, config.DefaultTokenExpiryBufferSeconds) {
		return false, nil // Not expired, no refresh needed
	}

	_, revoked, nativeErr := RefreshTokenNative(tokenPath, data)
	if revoked {
		return false, ErrTokenRevoked
	}
	if nativeErr != nil {
		// Tier 2 fallback: renew via headless agy CLI under storage file lock
		if fallbackErr := RefreshTokenFallbackAgy(store, tokenPath); fallbackErr != nil {
			return false, fmt.Errorf("native refresh failed (%v); fallback refresh failed: %w", nativeErr, fallbackErr)
		}
	}

	// If active, sync between live and vault
	if isActive {
		if tokenPath == store.LiveToken {
			_ = SyncActiveTokens(store.LiveToken, vaultToken)
		} else {
			_ = SyncActiveTokens(vaultToken, store.LiveToken)
		}
	}

	return true, nil
}

func RefreshTokenFallbackAgy(store *storage.StorageEngine, tokenPath string) error {
	agyPath, err := exec.LookPath("agy")
	if err != nil {
		return fmt.Errorf("agy CLI not found in PATH: %w", err)
	}

	if _, err := os.Stat(tokenPath); err != nil {
		return fmt.Errorf("token file does not exist: %w", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	return lock.WithLock(ctx, store.LockFile, config.DefaultLockTimeout, func() error {
		oldData, _ := ReadTokenData(tokenPath)
		oldAcc := ExtractAccessToken(oldData)
		oldExp := ExtractExpiry(oldData)

		absTokenPath, _ := filepath.Abs(tokenPath)
		absLiveToken, _ := filepath.Abs(store.LiveToken)

		if absTokenPath == absLiveToken {
			cmd := exec.CommandContext(ctx, agyPath, "models")
			_ = cmd.Run()

			newData, _ := ReadTokenData(store.LiveToken)
			newAcc := ExtractAccessToken(newData)
			newExp := ExtractExpiry(newData)
			if newAcc != "" && (oldAcc == "" || newAcc != oldAcc || newExp != oldExp) {
				return nil
			}
			return errors.New("headless agy execution did not renew live token")
		}

		_ = os.MkdirAll(filepath.Dir(store.LiveToken), 0755)
		backupPath := store.LiveToken + fmt.Sprintf(".fallback_bak_%d", os.Getpid())
		liveExisted := false
		if _, err := os.Stat(store.LiveToken); err == nil {
			liveExisted = true
			if err := copyFile(store.LiveToken, backupPath); err != nil {
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

		// Copy tokenPath into liveToken
		tmpLive := filepath.Join(filepath.Dir(store.LiveToken), fmt.Sprintf(".tmp_live_%d", os.Getpid()))
		if err := copyFile(tokenPath, tmpLive); err != nil {
			return fmt.Errorf("failed to copy token to temp live: %w", err)
		}
		if err := os.Rename(tmpLive, store.LiveToken); err != nil {
			return fmt.Errorf("failed to stage live token: %w", err)
		}

		// Run headless agy
		cmd := exec.CommandContext(ctx, agyPath, "models")
		_ = cmd.Run()

		newData, err := ReadTokenData(store.LiveToken)
		if err != nil {
			return fmt.Errorf("failed to read renewed live token: %w", err)
		}
		newAcc := ExtractAccessToken(newData)
		newExp := ExtractExpiry(newData)
		if newAcc == "" || (newAcc == oldAcc && newExp == oldExp) {
			return errors.New("headless agy execution did not renew token")
		}

		// Copy renewed token back to tokenPath
		tmpVault := tokenPath + fmt.Sprintf(".tmp_%d", os.Getpid())
		if err := copyFile(store.LiveToken, tmpVault); err != nil {
			return fmt.Errorf("failed to save renewed token: %w", err)
		}
		return os.Rename(tmpVault, tokenPath)
	})
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer out.Close()

	if _, err := io.Copy(out, in); err != nil {
		return err
	}
	return out.Sync()
}
