package quota

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/lock"
	"github.com/alirezaopmc/promux/pkg/models"
)

type CachedAccountEntry struct {
	CachedAt string               `json:"cached_at"`
	Quota    *models.QuotaSummary `json:"quota"`
}

type CacheData struct {
	Accounts map[string]*CachedAccountEntry `json:"accounts"`
}

type QuotaCache struct {
	Home      string
	CacheDir  string
	CacheFile string
	LockFile  string
	mu        sync.Mutex
}

func NewCache(home string) *QuotaCache {
	if home == "" {
		home = config.ToolHome("agy")
	}
	cacheDir := filepath.Join(home, "cache")
	return &QuotaCache{
		Home:      home,
		CacheDir:  cacheDir,
		CacheFile: filepath.Join(cacheDir, "quota.json"),
		LockFile:  filepath.Join(cacheDir, "quota.lock"),
	}
}

func (qc *QuotaCache) GetTTL() time.Duration {
	if val := os.Getenv("PROMUX_QUOTA_CACHE_TTL"); val != "" {
		if sec, err := strconv.Atoi(val); err == nil && sec >= 0 {
			return time.Duration(sec) * time.Second
		}
	}
	return time.Duration(config.DefaultQuotaCacheTTLSeconds) * time.Second
}

func (qc *QuotaCache) read() *CacheData {
	data, err := os.ReadFile(qc.CacheFile)
	if err != nil {
		return &CacheData{Accounts: make(map[string]*CachedAccountEntry)}
	}
	var res CacheData
	if err := json.Unmarshal(data, &res); err != nil || res.Accounts == nil {
		return &CacheData{Accounts: make(map[string]*CachedAccountEntry)}
	}
	return &res
}

func (qc *QuotaCache) write(data *CacheData) error {
	if err := os.MkdirAll(qc.CacheDir, 0755); err != nil {
		return err
	}
	tmpFile := qc.CacheFile + ".tmp"
	f, err := os.OpenFile(tmpFile, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}

	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(data); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpFile)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmpFile)
		return err
	}

	return os.Rename(tmpFile, qc.CacheFile)
}

func (qc *QuotaCache) Get(accountName string) (*models.QuotaSummary, bool) {
	qc.mu.Lock()
	defer qc.mu.Unlock()

	ttl := qc.GetTTL()
	if ttl == 0 {
		return nil, false
	}

	data := qc.read()
	entry, ok := data.Accounts[accountName]
	if !ok || entry == nil || entry.Quota == nil {
		return nil, false
	}

	cachedTime, err := time.Parse(time.RFC3339, entry.CachedAt)
	if err != nil {
		return nil, false
	}

	if time.Since(cachedTime) > ttl {
		return nil, false
	}

	return entry.Quota, true
}

func (qc *QuotaCache) Set(accountName string, qs *models.QuotaSummary) error {
	qc.mu.Lock()
	defer qc.mu.Unlock()

	return lock.WithLock(context.Background(), qc.LockFile, config.DefaultLockTimeout, func() error {
		data := qc.read()
		data.Accounts[accountName] = &CachedAccountEntry{
			CachedAt: time.Now().UTC().Format(time.RFC3339),
			Quota:    qs,
		}
		return qc.write(data)
	})
}

func (qc *QuotaCache) Invalidate(accountName string) error {
	qc.mu.Lock()
	defer qc.mu.Unlock()

	return lock.WithLock(context.Background(), qc.LockFile, config.DefaultLockTimeout, func() error {
		data := qc.read()
		if _, ok := data.Accounts[accountName]; ok {
			delete(data.Accounts, accountName)
			return qc.write(data)
		}
		return nil
	})
}
