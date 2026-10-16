package auth

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"time"
)

type RawTokenData map[string]any

func ReadTokenData(tokenPath string) (RawTokenData, error) {
	data, err := os.ReadFile(tokenPath)
	if err != nil {
		return nil, err
	}

	var raw RawTokenData
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}
	return raw, nil
}

func ExtractAccessToken(tokenData RawTokenData) string {
	if tokenData == nil {
		return ""
	}
	if tok, ok := tokenData["token"].(map[string]any); ok {
		if acc, ok := tok["access_token"].(string); ok {
			return acc
		}
	}
	if acc, ok := tokenData["access_token"].(string); ok {
		return acc
	}
	return ""
}

func ExtractRefreshToken(tokenData RawTokenData) string {
	if tokenData == nil {
		return ""
	}
	if tok, ok := tokenData["token"].(map[string]any); ok {
		if ref, ok := tok["refresh_token"].(string); ok {
			return ref
		}
	}
	if ref, ok := tokenData["refresh_token"].(string); ok {
		return ref
	}
	return ""
}

func ExtractExpiry(tokenData RawTokenData) string {
	if tokenData == nil {
		return ""
	}
	if tok, ok := tokenData["token"].(map[string]any); ok {
		if exp, ok := tok["expiry"].(string); ok {
			return exp
		}
	}
	if exp, ok := tokenData["expiry"].(string); ok {
		return exp
	}
	return ""
}

func ParseTokenExpiry(expiryStr string) (time.Time, bool) {
	if expiryStr == "" {
		return time.Time{}, false
	}
	layouts := []string{
		time.RFC3339Nano,
		time.RFC3339,
		"2006-01-02T15:04:05.999999",
		"2006-01-02T15:04:05",
	}
	for _, l := range layouts {
		if t, err := time.Parse(l, expiryStr); err == nil {
			return t.UTC(), true
		}
	}
	return time.Time{}, false
}

func IsTokenExpired(tokenData RawTokenData, bufferSeconds int) bool {
	if tokenData == nil {
		return true
	}
	expStr := ExtractExpiry(tokenData)
	expTime, ok := ParseTokenExpiry(expStr)
	if !ok {
		return true
	}

	threshold := time.Now().UTC().Add(time.Duration(bufferSeconds) * time.Second)
	return threshold.After(expTime) || threshold.Equal(expTime)
}

func AtomicCopyToken(src, dst string) error {
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

func SyncActiveTokens(src, dst string) error {
	if _, err := os.Stat(src); os.IsNotExist(err) {
		return nil
	}
	if _, err := os.Stat(dst); err == nil {
		srcData, _ := ReadTokenData(src)
		dstData, _ := ReadTokenData(dst)
		srcRef := ExtractRefreshToken(srcData)
		dstRef := ExtractRefreshToken(dstData)
		if srcRef != "" && dstRef != "" && srcRef != dstRef {
			return nil
		}
	}
	return AtomicCopyToken(src, dst)
}
