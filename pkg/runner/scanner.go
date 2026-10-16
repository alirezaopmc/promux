package runner

import (
	"bytes"
	"strings"
	"sync"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/watcher"
)

type OutputScanner struct {
	mu           sync.Mutex
	buf          bytes.Buffer
	onQuotaMatch func(match watcher.LogMatch)
}

func NewOutputScanner(onQuotaMatch func(match watcher.LogMatch)) *OutputScanner {
	return &OutputScanner{
		onQuotaMatch: onQuotaMatch,
	}
}

func (s *OutputScanner) Write(p []byte) (n int, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.buf.Write(p)

	// Process line by line
	for {
		line, err := s.buf.ReadString('\n')
		if err != nil {
			// Unfinished line, put back into buffer
			s.buf.WriteString(line)
			break
		}

		lineStr := strings.TrimRight(line, "\r\n")

		var pattern string
		if config.IndividualQuotaRegex.MatchString(lineStr) {
			pattern = "INDIVIDUAL_QUOTA"
		} else if config.ResourceExhaustedRegex.MatchString(lineStr) {
			pattern = "RESOURCE_EXHAUSTED"
		} else if config.WeeklyQuotaRegex.MatchString(lineStr) {
			pattern = "WEEKLY_QUOTA"
		}

		if pattern != "" && s.onQuotaMatch != nil {
			var hint string
			if sub := config.ResetHintRegex.FindStringSubmatch(lineStr); len(sub) > 1 {
				hint = sub[1]
			}
			s.onQuotaMatch(watcher.LogMatch{
				Pattern:   pattern,
				Line:      lineStr,
				ResetHint: hint,
			})
		}
	}

	return len(p), nil
}
