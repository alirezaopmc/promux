package watcher

import (
	"regexp"
	"strconv"
	"strings"

	"github.com/alirezaopmc/promux/pkg/config"
)

type LogMatch struct {
	Pattern   string
	Line      string
	ResetHint string
	FilePath  string
}

var (
	daysRegex  = regexp.MustCompile(`(\d+)\s*d(?:ay)?`)
	hoursRegex = regexp.MustCompile(`(\d+)\s*h(?:our)?`)
	minsRegex  = regexp.MustCompile(`(\d+)\s*m(?:in)?(?:ute)?`)
)

func ParseResetMinutes(hint string) int {
	if hint == "" {
		return config.DefaultCooldownMinutes
	}

	cleanHint := strings.TrimSpace(strings.ToLower(hint))
	cleanHint = strings.TrimPrefix(cleanHint, "~")
	cleanHint = strings.TrimSpace(cleanHint)

	days := 0
	if m := daysRegex.FindStringSubmatch(cleanHint); len(m) > 1 {
		if v, err := strconv.Atoi(m[1]); err == nil {
			days = v
		}
	}

	hours := 0
	if m := hoursRegex.FindStringSubmatch(cleanHint); len(m) > 1 {
		if v, err := strconv.Atoi(m[1]); err == nil {
			hours = v
		}
	}

	mins := 0
	if m := minsRegex.FindStringSubmatch(cleanHint); len(m) > 1 {
		if v, err := strconv.Atoi(m[1]); err == nil {
			mins = v
		}
	}

	total := (days * 24 * 60) + (hours * 60) + mins
	if total <= 0 {
		return config.DefaultCooldownMinutes
	}
	return total
}
