package auth

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"

	"github.com/alirezaopmc/promux/pkg/storage"
)

// HubSyncState describes the synchronization state between the Hub live token and promux's vault.
type HubSyncState string

const (
	HubSyncInSync    HubSyncState = "in-sync"
	HubSyncOutOfSync HubSyncState = "out-of-sync"
	HubSyncMissing   HubSyncState = "missing"
	HubSyncUntracked HubSyncState = "untracked"
)

// HubSyncInfo holds the Hub live token's sync state and extracted metadata.
type HubSyncInfo struct {
	State      HubSyncState `json:"state"`
	LiveEmail  string       `json:"live_email,omitempty"`
	LiveExpiry string       `json:"live_expiry,omitempty"`
	WriterID   string       `json:"writer_id,omitempty"`
}

func generateWriterID() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func writeJSONAtomic(targetPath string, data RawTokenData) error {
	if err := os.MkdirAll(filepath.Dir(targetPath), 0755); err != nil {
		return err
	}
	tmpPath := targetPath + ".tmp"
	f, err := os.OpenFile(tmpPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(data); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	if err := os.Rename(tmpPath, targetPath); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	return nil
}

// SyncVaultToHubToken writes a vault token to the Hub live token path, preserving any existing writer_id
// or generating a new 43-character URL-safe writer_id if none is present.
func SyncVaultToHubToken(vaultTokenPath, hubTokenPath string) (string, error) {
	vaultData, err := ReadTokenData(vaultTokenPath)
	if err != nil {
		return "", fmt.Errorf("failed to read vault token: %w", err)
	}
	if ExtractAccessToken(vaultData) == "" && ExtractRefreshToken(vaultData) == "" {
		return "", errors.New("vault token contains neither access_token nor refresh_token")
	}

	writerID := ""
	if existing, err := ReadTokenData(hubTokenPath); err == nil {
		if w, ok := existing["writer_id"].(string); ok && w != "" {
			writerID = w
		}
	}
	if writerID == "" {
		writerID, err = generateWriterID()
		if err != nil {
			return "", fmt.Errorf("failed to generate writer_id: %w", err)
		}
	}

	out := maps.Clone(vaultData)
	out["writer_id"] = writerID
	if err := writeJSONAtomic(hubTokenPath, out); err != nil {
		return "", fmt.Errorf("failed to write hub token: %w", err)
	}
	return writerID, nil
}

// ReadHubTokenForVault reads the Hub live token, strips writer_id, and returns the clean map,
// extracted email, and pretty-printed JSON bytes suitable for vault storage.
func ReadHubTokenForVault(hubTokenPath string) (RawTokenData, string, []byte, error) {
	data, err := ReadTokenData(hubTokenPath)
	if err != nil {
		return nil, "", nil, fmt.Errorf("failed to read hub token at %s: %w", hubTokenPath, err)
	}
	if ExtractAccessToken(data) == "" && ExtractRefreshToken(data) == "" {
		return nil, "", nil, errors.New("hub token contains neither access_token nor refresh_token")
	}
	email, _ := data["id_token_email"].(string)
	clean := maps.Clone(data)
	delete(clean, "writer_id")
	encoded, err := json.MarshalIndent(clean, "", "  ")
	if err != nil {
		return nil, "", nil, err
	}
	encoded = append(encoded, '\n')
	return clean, email, encoded, nil
}

// SyncHubToVaultIfSameAccount copies the Hub live token (without writer_id) to the vault token
// when both exist and share the same non-empty refresh_token.
func SyncHubToVaultIfSameAccount(hubTokenPath, vaultTokenPath string) error {
	hubData, err := ReadTokenData(hubTokenPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return fmt.Errorf("failed to read hub token: %w", err)
	}
	vaultData, err := ReadTokenData(vaultTokenPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return fmt.Errorf("failed to read vault token: %w", err)
	}
	hubRef := ExtractRefreshToken(hubData)
	vaultRef := ExtractRefreshToken(vaultData)
	if hubRef == "" || vaultRef == "" || hubRef != vaultRef {
		return nil
	}
	clean := maps.Clone(hubData)
	delete(clean, "writer_id")
	return writeJSONAtomic(vaultTokenPath, clean)
}

// CheckHubSyncStatus compares the Hub live token against the tracked HubActive profile in the vault.
func CheckHubSyncStatus(store *storage.StorageEngine, hubTokenPath string) (*HubSyncInfo, error) {
	info := &HubSyncInfo{State: HubSyncMissing}
	hubData, err := ReadTokenData(hubTokenPath)
	if err != nil {
		if os.IsNotExist(err) {
			return info, nil
		}
		return nil, err
	}
	info.LiveEmail, _ = hubData["id_token_email"].(string)
	info.LiveExpiry = ExtractExpiry(hubData)
	info.WriterID, _ = hubData["writer_id"].(string)

	state, err := store.LoadState()
	if err != nil {
		return nil, err
	}
	if state.HubActive == nil || *state.HubActive == "" {
		info.State = HubSyncUntracked
		return info, nil
	}
	vaultPath := filepath.Join(store.AccountsDir, *state.HubActive, "antigravity-oauth-token")
	vaultData, err := ReadTokenData(vaultPath)
	if err != nil {
		info.State = HubSyncOutOfSync
		return info, nil
	}
	hubRef := ExtractRefreshToken(hubData)
	vaultRef := ExtractRefreshToken(vaultData)
	if hubRef != "" && vaultRef != "" && hubRef == vaultRef {
		info.State = HubSyncInSync
		return info, nil
	}
	vaultEmail, _ := vaultData["id_token_email"].(string)
	if hubRef == "" && vaultEmail != "" && info.LiveEmail == vaultEmail {
		info.State = HubSyncInSync
		return info, nil
	}
	info.State = HubSyncOutOfSync
	return info, nil
}
