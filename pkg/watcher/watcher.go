package watcher

import (
	"bufio"
	"context"
	"io"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/alirezaopmc/promux/pkg/auth"
	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/failover"
	"github.com/alirezaopmc/promux/pkg/storage"
)

type LogWatcher struct {
	Storage      *storage.StorageEngine
	Failover     *failover.FailoverEngine
	GeminiHome   string
	CustomLogs   []string
	PollInterval time.Duration
	offsets      map[string]int64
	inodes       map[string]uint64
	mu           sync.Mutex
}

func NewLogWatcher(storage *storage.StorageEngine, failover *failover.FailoverEngine, geminiHome string) *LogWatcher {
	if geminiHome == "" && storage != nil {
		geminiHome = storage.GeminiHome
	}
	if geminiHome == "" {
		geminiHome = config.GeminiHome()
	}

	return &LogWatcher{
		Storage:      storage,
		Failover:     failover,
		GeminiHome:   geminiHome,
		PollInterval: 1 * time.Second,
		offsets:      make(map[string]int64),
		inodes:       make(map[string]uint64),
	}
}

func (w *LogWatcher) GetLogFiles() []string {
	if len(w.CustomLogs) > 0 {
		var res []string
		for _, f := range w.CustomLogs {
			if _, err := os.Stat(f); err == nil {
				res = append(res, f)
			}
		}
		return res
	}

	var files []string
	cliLog := config.CLILogFile(w.GeminiHome)
	if _, err := os.Stat(cliLog); err == nil {
		files = append(files, cliLog)
	}

	logDir := config.LogDir(w.GeminiHome)
	entries, err := os.ReadDir(logDir)
	if err == nil {
		type fileWithTime struct {
			path  string
			mtime time.Time
		}
		var logs []fileWithTime
		for _, e := range entries {
			if !e.IsDir() && filepath.Ext(e.Name()) == ".log" {
				p := filepath.Join(logDir, e.Name())
				if fi, err := e.Info(); err == nil {
					logs = append(logs, fileWithTime{path: p, mtime: fi.ModTime()})
				}
			}
		}

		sort.Slice(logs, func(i, j int) bool {
			return logs[i].mtime.After(logs[j].mtime)
		})

		count := 5
		if len(logs) < count {
			count = len(logs)
		}
		for i := 0; i < count; i++ {
			files = append(files, logs[i].path)
		}
	}

	// Deduplicate
	seen := make(map[string]bool)
	var deduped []string
	for _, f := range files {
		if !seen[f] {
			seen[f] = true
			deduped = append(deduped, f)
		}
	}
	return deduped
}

func (w *LogWatcher) InitOffsets() {
	w.mu.Lock()
	defer w.mu.Unlock()

	for _, f := range w.GetLogFiles() {
		fi, err := os.Stat(f)
		if err == nil {
			w.offsets[f] = fi.Size()
			w.inodes[f] = getFileInode(fi)
		} else {
			w.offsets[f] = 0
			w.inodes[f] = 0
		}
	}
}

func (w *LogWatcher) RunOnce() ([]LogMatch, error) {
	w.mu.Lock()
	defer w.mu.Unlock()

	var matches []LogMatch
	files := w.GetLogFiles()

	for _, path := range files {
		fi, err := os.Stat(path)
		if err != nil {
			continue
		}

		size := fi.Size()
		ino := getFileInode(fi)

		prevOffset, ok := w.offsets[path]
		prevIno := w.inodes[path]

		// Handle rotation or truncation
		if !ok || size < prevOffset || (ino != 0 && prevIno != 0 && ino != prevIno) {
			prevOffset = 0
		}

		if size == prevOffset {
			w.inodes[path] = ino
			continue
		}

		f, err := os.Open(path)
		if err != nil {
			continue
		}

		if _, err := f.Seek(prevOffset, io.SeekStart); err != nil {
			_ = f.Close()
			continue
		}

		scanner := bufio.NewScanner(f)
		for scanner.Scan() {
			line := scanner.Text()

			var pattern string
			if config.IndividualQuotaRegex.MatchString(line) {
				pattern = "INDIVIDUAL_QUOTA"
			} else if config.ResourceExhaustedRegex.MatchString(line) {
				pattern = "RESOURCE_EXHAUSTED"
			} else if config.WeeklyQuotaRegex.MatchString(line) {
				pattern = "WEEKLY_QUOTA"
			}

			if pattern != "" {
				var hint string
				if sub := config.ResetHintRegex.FindStringSubmatch(line); len(sub) > 1 {
					hint = sub[1]
				}
				matches = append(matches, LogMatch{
					Pattern:   pattern,
					Line:      line,
					ResetHint: hint,
					FilePath:  path,
				})
			}
		}

		_ = f.Close()
		w.offsets[path] = size
		w.inodes[path] = ino
	}

	return matches, nil
}

func (w *LogWatcher) RunForever(ctx context.Context, onMatch func(LogMatch)) error {
	w.InitOffsets()

	ticker := time.NewTicker(w.PollInterval)
	defer ticker.Stop()

	tokenCheckTicker := time.NewTicker(15 * time.Minute)
	defer tokenCheckTicker.Stop()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-tokenCheckTicker.C:
			if w.Storage != nil {
				active, _ := w.Storage.GetActiveProfile()
				if active != nil {
					_, _ = auth.RefreshAccountToken(w.Storage, active.Name, false)
				}
			}
		case <-ticker.C:
			matches, err := w.RunOnce()
			if err != nil {
				continue
			}

			for _, m := range matches {
				if onMatch != nil {
					onMatch(m)
				}
				if w.Failover != nil {
					mins := ParseResetMinutes(m.ResetHint)
					_, _ = w.Failover.RotateNext("reactive: "+m.Pattern, mins)
					break // break out of multiple matches in same tick
				}
			}
		}
	}
}
