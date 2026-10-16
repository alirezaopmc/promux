package terminal

import (
	"bytes"
	"strings"
	"testing"

	"github.com/fatih/color"
)

func TestVisibleWidth(t *testing.T) {
	colored := color.GreenString("100.0%")
	if VisibleWidth(colored) != 6 {
		t.Fatalf("expected visible width 6 for colored '100.0%%', got %d", VisibleWidth(colored))
	}

	mixed := color.RedString("ERROR") + " message"
	if VisibleWidth(mixed) != 13 {
		t.Fatalf("expected visible width 13 for 'ERROR message', got %d", VisibleWidth(mixed))
	}
}

func TestTableAlignmentWithANSI(t *testing.T) {
	tbl := NewTable("  PROFILE", "GEMINI (5H)", "STATUS")
	tbl.AddRow("* aj", color.GreenString("100.0%"), color.GreenString("active"))
	tbl.AddRow("  opmc", color.GreenString("100.0% (4h 58m)"), color.YellowString("cooling_down"))

	var buf bytes.Buffer
	if err := tbl.Render(&buf); err != nil {
		t.Fatalf("render failed: %v", err)
	}

	lines := strings.Split(strings.TrimRight(buf.String(), "\n"), "\n")
	if len(lines) != 3 {
		t.Fatalf("expected 3 lines, got %d: %q", len(lines), lines)
	}

	// Verify all rows have the second column start at the exact same visual position
	// Column 0 max width: "  PROFILE" (9) + gap (2) = 11.
	for i, line := range lines {
		clean := StripANSI(line)
		pos := strings.Index(clean, "GEMINI")
		if i == 0 && pos != 11 {
			t.Fatalf("header GEMINI should start at index 11, got %d in line: %q", pos, clean)
		}
		if i == 1 {
			pos1 := strings.Index(clean, "100.0%")
			if pos1 != 11 {
				t.Fatalf("row 1 100.0%% should start at index 11, got %d in line: %q", pos1, clean)
			}
		}
		if i == 2 {
			pos2 := strings.Index(clean, "100.0% (4h 58m)")
			if pos2 != 11 {
				t.Fatalf("row 2 100.0%% (4h 58m) should start at index 11, got %d in line: %q", pos2, clean)
			}
		}
	}
}
