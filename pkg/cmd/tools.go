package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/fatih/color"
	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/terminal"
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
			out := cmd.OutOrStdout()
			if JSONOut {
				return json.NewEncoder(out).Encode(SupportedTools)
			}

			tbl := terminal.NewTable("  TOOL", "NAME", "STATUS", "CAPABILITIES")

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
				tbl.AddRow(fmt.Sprintf("%s %s", prefix, t.Name), t.DisplayName, statusStr, caps)
			}
			return tbl.Render(out)
		},
	}
}
