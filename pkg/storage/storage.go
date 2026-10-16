package storage

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/lock"
	"github.com/alirezaopmc/promux/pkg/models"
)

var (
	ErrAccountNotFound   = errors.New("account not found")
	ErrLiveTokenNotFound = errors.New("live token not found")
	ErrSrcTokenNotFound  = errors.New("source token not found")
)

type StorageEngine struct {
	Home        string
	GeminiHome  string
	AccountsDir string
	StateFile   string
	LockFile    string
	LiveToken   string
}

func New(promuxHome, geminiHome string) *StorageEngine {
	if promuxHome == "" {
		promuxHome = config.ToolHome("agy")
	}
	if geminiHome == "" {
		geminiHome = config.GeminiHome()
	}

	return &StorageEngine{
		Home:        promuxHome,
		GeminiHome:  geminiHome,
		AccountsDir: filepath.Join(promuxHome, "accounts"),
		StateFile:   filepath.Join(promuxHome, "state.json"),
		LockFile:    filepath.Join(promuxHome, "manager.lock"),
		LiveToken:   config.LiveTokenFile(geminiHome),
	}
}

func (s *StorageEngine) atomicWriteJSON(targetPath string, data any) error {
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
	if err := f.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}

	return os.Rename(tmpPath, targetPath)
}

func (s *StorageEngine) atomicCopyFile(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
		return err
	}

	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	tmpPath := dst + ".tmp"
	out, err := os.OpenFile(tmpPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}

	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := out.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}

	return os.Rename(tmpPath, dst)
}

func (s *StorageEngine) LoadState() (*models.State, error) {
	if _, err := os.Stat(s.StateFile); os.IsNotExist(err) {
		return models.NewState(), nil
	}

	data, err := os.ReadFile(s.StateFile)
	if err != nil {
		return models.NewState(), nil
	}

	state := models.NewState()
	if err := json.Unmarshal(data, state); err != nil {
		return models.NewState(), nil
	}
	if state.Accounts == nil {
		state.Accounts = make(map[string]*models.AccountMeta)
	}
	return state, nil
}

func (s *StorageEngine) SaveState(state *models.State) error {
	return lock.WithLock(context.Background(), s.LockFile, config.DefaultLockTimeout, func() error {
		return s.atomicWriteJSON(s.StateFile, state)
	})
}

func (s *StorageEngine) Transaction(ctx context.Context, fn func(state *models.State) error) error {
	return lock.WithLock(ctx, s.LockFile, config.DefaultLockTimeout, func() error {
		state, err := s.LoadState()
		if err != nil {
			return err
		}

		if err := fn(state); err != nil {
			return err
		}

		return s.atomicWriteJSON(s.StateFile, state)
	})
}

func (s *StorageEngine) SaveProfile(name string, email, projectID *string, planType string) (*models.AccountMeta, error) {
	if _, err := os.Stat(s.LiveToken); os.IsNotExist(err) {
		return nil, fmt.Errorf("%w at %s", ErrLiveTokenNotFound, s.LiveToken)
	}

	if planType == "" {
		planType = "STANDARD"
	}

	var savedAcct *models.AccountMeta

	err := s.Transaction(context.Background(), func(state *models.State) error {
		acctDir := filepath.Join(s.AccountsDir, name)
		destToken := filepath.Join(acctDir, "antigravity-oauth-token")

		if err := s.atomicCopyFile(s.LiveToken, destToken); err != nil {
			return fmt.Errorf("failed to copy live token to vault: %w", err)
		}

		now := time.Now().UTC()
		acct := &models.AccountMeta{
			Name:       name,
			Enabled:    true,
			SavedAt:    &now,
			LastUsedAt: &now,
			Email:      email,
			ProjectID:  projectID,
			PlanType:   planType,
		}

		state.Accounts[name] = acct
		if state.Active == nil || *state.Active == "" {
			state.Active = &name
		}
		savedAcct = acct
		return nil
	})

	return savedAcct, err
}

func (s *StorageEngine) SwitchProfile(name string) error {
	return s.Transaction(context.Background(), func(state *models.State) error {
		acct, ok := state.Accounts[name]
		if !ok {
			return fmt.Errorf("%w: %s", ErrAccountNotFound, name)
		}

		srcToken := filepath.Join(s.AccountsDir, name, "antigravity-oauth-token")
		if _, err := os.Stat(srcToken); os.IsNotExist(err) {
			return fmt.Errorf("%w at %s", ErrSrcTokenNotFound, srcToken)
		}

		if err := s.atomicCopyFile(srcToken, s.LiveToken); err != nil {
			return fmt.Errorf("failed to swap token: %w", err)
		}

		now := time.Now().UTC()
		acct.LastUsedAt = &now
		state.Active = &name
		return nil
	})
}

func (s *StorageEngine) RemoveProfile(name string) error {
	return s.Transaction(context.Background(), func(state *models.State) error {
		if _, ok := state.Accounts[name]; !ok {
			return fmt.Errorf("%w: %s", ErrAccountNotFound, name)
		}

		delete(state.Accounts, name)
		if state.Active != nil && *state.Active == name {
			state.Active = nil
		}

		acctDir := filepath.Join(s.AccountsDir, name)
		_ = os.RemoveAll(acctDir)
		return nil
	})
}

func (s *StorageEngine) GetActiveProfile() (*models.AccountMeta, error) {
	state, err := s.LoadState()
	if err != nil {
		return nil, err
	}
	if state.Active == nil {
		return nil, nil
	}
	return state.Accounts[*state.Active], nil
}

func (s *StorageEngine) GetProfile(name string) (*models.AccountMeta, error) {
	state, err := s.LoadState()
	if err != nil {
		return nil, err
	}
	acct, ok := state.Accounts[name]
	if !ok {
		return nil, fmt.Errorf("%w: %s", ErrAccountNotFound, name)
	}
	return acct, nil
}
