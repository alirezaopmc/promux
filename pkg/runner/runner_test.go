package runner

import (
	"testing"

	"github.com/alirezaopmc/promux/pkg/watcher"
)

func TestOutputScannerMatching(t *testing.T) {
	var matches []watcher.LogMatch

	scanner := NewOutputScanner(func(match watcher.LogMatch) {
		matches = append(matches, match)
	})

	// Stream chunks as if coming from PTY
	chunks := []string{
		"Thinking...\n",
		"Error: Individual quota reached. Resets in ~",
		"1h 30m.\nWaiting for user input.\n",
		"Next turn...\n",
		"RESOURCE_EXHAUSTED (code 429)\n",
	}

	for _, c := range chunks {
		n, err := scanner.Write([]byte(c))
		if err != nil {
			t.Fatalf("scanner write error: %v", err)
		}
		if n != len(c) {
			t.Fatalf("scanner wrote %d bytes, expected %d", n, len(c))
		}
	}

	if len(matches) != 2 {
		t.Fatalf("expected 2 matches, got %d", len(matches))
	}

	if matches[0].Pattern != "INDIVIDUAL_QUOTA" || matches[0].ResetHint != "~1h 30m" {
		t.Fatalf("unexpected match 0: %+v", matches[0])
	}

	if matches[1].Pattern != "RESOURCE_EXHAUSTED" {
		t.Fatalf("unexpected match 1: %+v", matches[1])
	}
}
