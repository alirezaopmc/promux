package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"text/tabwriter"

	"github.com/fatih/color"
	"github.com/spf13/cobra"
)

type ToolInfo struct {
	Name        string   `json:"name"`
	DisplayName string   `json:"display_name"`
	Status      string   `json:"status"`
	Features    []string `json:"features"`
}

var SupportedTools = []ToolInfo{
	{
		Name:        "agy",
		DisplayName: "Google Antigravity CLI",
		Status:      "active",
		Features:    []string{"vault", "quota", "watch", "run"},
	},
	{
		Name:        "claude",
		DisplayName: "Anthropic Claude Code",
		Status:      "supported",
		Features:    []string{"vault", "watch"},
	},
	{
		Name:        "codex",
		DisplayName: "OpenAI Codex CLI",
		Status:      "supported",
		Features:    []string{"vault"},
	},
	{
		Name:        "cursor",
		DisplayName: "Cursor CLI",
		Status:      "supported",
		Features:    []string{"vault"},
	},
}

func newToolsCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "tools [list]",
		Short: "List supported developer AI CLI tools and capabilities",
		RunE: func(cmd *cobra.Command, args []string) error {
			if JSONOut {
				return json.NewEncoder(os.Stdout).Encode(SupportedTools)
			}

			w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
			fmt.Fprintln(w, "  TOOL\tNAME\tSTATUS\tCAPABILITIES")

			for _, t := range SupportedTools {
				prefix := " "
				if t.Name == ToolName {
					prefix = color.GreenString("✓")
				}
				statusStr := t.Status
				if t.Status == "active" {
					statusStr = color.GreenString(statusStr)
				}
				caps := fmt.Sprintf("%v", t.Features)
				fmt.Fprintf(w, "%s %s\t%s\t%s\t%s\n", prefix, t.Name, t.DisplayName, statusStr, caps)
			}
			return w.Flush()
		},
	}
}
