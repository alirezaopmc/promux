package terminal

import (
	"io"
	"regexp"
	"strings"
	"unicode/utf8"
)

var ansiRegex = regexp.MustCompile(`\x1b\[[0-9;]*[a-zA-Z]`)

// VisibleWidth returns the visual character width of a string, ignoring ANSI escape sequences.
func VisibleWidth(s string) int {
	clean := ansiRegex.ReplaceAllString(s, "")
	return utf8.RuneCountInString(clean)
}

// StripANSI removes all ANSI escape sequences from a string.
func StripANSI(s string) string {
	return ansiRegex.ReplaceAllString(s, "")
}

// Table represents a terminal table with ANSI-aware column width calculation.
type Table struct {
	headers []string
	rows    [][]string
	gap     int
}

// NewTable creates a new Table with given column headers.
func NewTable(headers ...string) *Table {
	return &Table{
		headers: headers,
		gap:     2,
	}
}

// SetGap sets the spacing between adjacent columns (default is 2).
func (t *Table) SetGap(gap int) {
	if gap >= 1 {
		t.gap = gap
	}
}

// AddRow appends a row of cells to the table.
func (t *Table) AddRow(cells ...string) {
	t.rows = append(t.rows, cells)
}

// Render calculates exact visible widths and prints the aligned table to w.
func (t *Table) Render(w io.Writer) error {
	numCols := len(t.headers)
	for _, r := range t.rows {
		if len(r) > numCols {
			numCols = len(r)
		}
	}

	if numCols == 0 {
		return nil
	}

	colWidths := make([]int, numCols)
	for i, h := range t.headers {
		w := VisibleWidth(h)
		if w > colWidths[i] {
			colWidths[i] = w
		}
	}

	for _, r := range t.rows {
		for i, cell := range r {
			if i < numCols {
				w := VisibleWidth(cell)
				if w > colWidths[i] {
					colWidths[i] = w
				}
			}
		}
	}

	renderRow := func(cells []string) error {
		var b strings.Builder
		for i, cell := range cells {
			b.WriteString(cell)
			if i < len(cells)-1 {
				vis := VisibleWidth(cell)
				pad := colWidths[i] - vis + t.gap
				if pad > 0 {
					b.WriteString(strings.Repeat(" ", pad))
				}
			}
		}
		b.WriteString("\n")
		_, err := io.WriteString(w, b.String())
		return err
	}

	if len(t.headers) > 0 {
		if err := renderRow(t.headers); err != nil {
			return err
		}
	}

	for _, r := range t.rows {
		if err := renderRow(r); err != nil {
			return err
		}
	}

	return nil
}

// String renders the table and returns the resulting string.
func (t *Table) String() string {
	var b strings.Builder
	_ = t.Render(&b)
	return b.String()
}
