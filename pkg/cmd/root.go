package cmd

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/storage"
)

var (
	Version   = "1.1.3"
	ToolName  = "agy"
	JSONOut   = false
	DebugMode = false
)

func GetStorage() *storage.StorageEngine {
	toolHome := config.ToolHome(ToolName)
	return storage.New(toolHome, config.GeminiHome())
}

var RootCmd = &cobra.Command{
	Use:   "promux [tool] [command]",
	Short: "Promux: AI CLI Profile Multiplexer & Quota Failover Daemon",
	Long: `Promux is an extensible, zero-runtime-dependency profile multiplexer
and quota-aware failover engine for developer AI CLI tools (Antigravity, Claude, Codex, Cursor).`,
	SilenceUsage:  true,
	SilenceErrors: true,
}

func init() {
	RootCmd.PersistentFlags().StringVarP(&ToolName, "tool", "t", "agy", "Target tool (agy, claude, codex, cursor)")
	RootCmd.PersistentFlags().BoolVar(&JSONOut, "json", false, "Output results in JSON format")
	RootCmd.PersistentFlags().BoolVar(&DebugMode, "debug", false, "Enable debug output")

	RootCmd.AddCommand(newProfileCmd())
	RootCmd.AddCommand(newQuotaCmd())
	RootCmd.AddCommand(newPrimeCmd())
	RootCmd.AddCommand(newWatchCmd())
	RootCmd.AddCommand(newRunCmd())
	RootCmd.AddCommand(newToolsCmd())
	RootCmd.AddCommand(newVersionCmd())

	// Top-level aliases for default agy usage
	RootCmd.AddCommand(newListCmd())
	RootCmd.AddCommand(newSaveCmd())
	RootCmd.AddCommand(newSwitchCmd())
	RootCmd.AddCommand(newNextCmd())
	RootCmd.AddCommand(newWhoamiCmd())
	RootCmd.AddCommand(newRemoveCmd())
	RootCmd.AddCommand(newRefreshCmd())
}

func Execute() {
	if err := RootCmd.Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}
