package quota

import (
	"fmt"
	"math"
	"time"
)

func ParseResetTimestamp(isoStr string) (time.Time, bool) {
	if isoStr == "" {
		return time.Time{}, false
	}
	layouts := []string{
		time.RFC3339Nano,
		time.RFC3339,
		"2006-01-02T15:04:05.999999Z",
		"2006-01-02T15:04:05Z",
	}
	for _, l := range layouts {
		if t, err := time.Parse(l, isoStr); err == nil {
			return t.UTC(), true
		}
	}
	return time.Time{}, false
}

func FormatResetDuration(target time.Time, now time.Time) string {
	diff := target.Sub(now)
	if diff <= 0 {
		return "now"
	}

	days := int(diff.Hours()) / 24
	hours := int(diff.Hours()) % 24
	mins := int(diff.Minutes()) % 60

	if days > 0 {
		if hours > 0 {
			return fmt.Sprintf("%dd %dh", days, hours)
		}
		return fmt.Sprintf("%dd", days)
	}

	if hours > 0 {
		if mins > 0 {
			return fmt.Sprintf("%dh %dm", hours, mins)
		}
		return fmt.Sprintf("%dh", hours)
	}

	if mins > 0 {
		return fmt.Sprintf("%dm", mins)
	}

	return "<1m"
}

func FormatPercentage(fraction float64) string {
	pct := math.Max(0.0, math.Min(100.0, fraction*100.0))
	return fmt.Sprintf("%.1f%%", pct)
}
