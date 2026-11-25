package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/fatih/color"
	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/auth"
	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/hub"
	"github.com/alirezaopmc/promux/pkg/storage"
	"github.com/alirezaopmc/promux/pkg/terminal"
)

func requireAgyTool() error {
	if ToolName != "agy" {
		return fmt.Errorf("hub is only supported for tool 'agy'")
	}
	return nil
}

func cmdContext(cmd *cobra.Command) context.Context {
	if ctx := cmd.Context(); ctx != nil {
		return ctx
	}
	return context.Background()
}

func writeJSON(w io.Writer, v any) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func ensureProfileTokenFresh(store *storage.StorageEngine, name string) (bool, error) {
	if _, err := store.GetProfile(name); err != nil {
		return false, err
	}

	vaultToken := filepath.Join(store.AccountsDir, name, "antigravity-oauth-token")
	tokenData, err := auth.ReadTokenData(vaultToken)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return false, fmt.Errorf("%w at %s", storage.ErrSrcTokenNotFound, vaultToken)
		}
		return false, fmt.Errorf("failed to read vault token for %s: %w", name, err)
	}

	if auth.ExtractAccessToken(tokenData) != "" && !auth.IsTokenExpired(tokenData, config.DefaultTokenExpiryBufferSeconds) {
		return false, nil
	}

	refreshed, err := auth.RefreshAccountToken(store, name, true)
	if err != nil {
		return false, fmt.Errorf("failed to refresh token for %s: %w", name, err)
	}
	return refreshed, nil
}

const hubReconnectNote = "Hub language_server did not auto-respawn within timeout; Antigravity GUI will reconnect automatically or on window reload."

func printHubRestartSummary(out, errOut io.Writer, restart *hub.RestartResult, noRestart bool) {
	if JSONOut {
		if restart != nil && restart.NeedsReconnect {
			fmt.Fprintf(errOut, "note: %s\n", hubReconnectNote)
		}
		return
	}

	if noRestart {
		fmt.Fprintln(out, "  • Hub daemon restart skipped (--no-restart)")
		return
	}
	if restart == nil {
		return
	}
	if restart.Deferred {
		fmt.Fprintf(out, "  • Scheduled deferred restart for Hub daemon PID(s): %v\n", restart.DeferredPIDs)
		return
	}
	if len(restart.RespawnedPIDs) > 0 {
		fmt.Fprintf(out, "  • Restarted Hub daemon (stopped %v, respawned %v)\n", restart.StoppedPIDs, restart.RespawnedPIDs)
		return
	}
	if len(restart.StoppedPIDs) > 0 {
		fmt.Fprintf(out, "  • Stopped Hub daemon PID(s): %v\n", restart.StoppedPIDs)
	} else {
		fmt.Fprintln(out, "  • No running Hub language_server daemons found")
	}
	if restart.NeedsReconnect {
		fmt.Fprintf(out, "  • Note: %s\n", hubReconnectNote)
	}
}

func emitHubSyncStatusWarning(errOut io.Writer, st *hub.StatusResult) {
	if !JSONOut || st == nil || st.Sync == nil || st.Sync.State != auth.HubSyncOutOfSync {
		return
	}
	tracked := "none"
	if st.HubActive != nil && *st.HubActive != "" {
		tracked = *st.HubActive
	}
	fmt.Fprintf(errOut, "warning: Hub live token (%s) is out-of-sync with tracked profile %q\n", st.Sync.LiveEmail, tracked)
}

func renderHubStatus(out io.Writer, st *hub.StatusResult, detailed bool) error {
	activeDisplay := "none"
	if st.HubActive != nil && *st.HubActive != "" {
		activeDisplay = color.GreenString(*st.HubActive)
	}

	email := "-"
	if st.Email != "" {
		email = st.Email
	}
	plan := "-"
	if st.PlanType != "" {
		plan = st.PlanType
	}

	syncDisplay := string(auth.HubSyncMissing)
	liveEmail := "-"
	liveExpiry := "-"
	var syncState auth.HubSyncState
	if st.Sync != nil {
		syncState = st.Sync.State
		syncDisplay = string(syncState)
		switch syncState {
		case auth.HubSyncInSync:
			syncDisplay = color.GreenString(syncDisplay)
		case auth.HubSyncOutOfSync, auth.HubSyncUntracked:
			syncDisplay = color.YellowString(syncDisplay)
		case auth.HubSyncMissing:
			syncDisplay = color.RedString(syncDisplay)
		}
		if st.Sync.LiveEmail != "" {
			liveEmail = st.Sync.LiveEmail
		}
		if st.Sync.LiveExpiry != "" {
			liveExpiry = st.Sync.LiveExpiry
		}
	}

	fmt.Fprintf(out, "Hub Active Profile: %s\n", activeDisplay)
	fmt.Fprintf(out, "Email:              %s\n", email)
	fmt.Fprintf(out, "Plan:               %s\n", plan)
	fmt.Fprintf(out, "Sync Status:        %s\n", syncDisplay)
	if syncState == auth.HubSyncOutOfSync {
		fmt.Fprintf(out, "  ⚠ Live Hub token (%s) differs from vaulted profile; run 'promux hub save <name>' or 'promux hub switch <name>'.\n", liveEmail)
	}

	if detailed {
		fmt.Fprintf(out, "Hub Token Path:     %s\n", st.HubTokenPath)
		fmt.Fprintf(out, "Live Token Email:   %s\n", liveEmail)
		fmt.Fprintf(out, "Live Token Expiry:  %s\n", liveExpiry)
	}

	if len(st.Daemons) == 0 {
		fmt.Fprintln(out, "Hub Daemons:        none running")
		return nil
	}

	if !detailed {
		pidStrs := make([]string, 0, len(st.Daemons))
		for _, d := range st.Daemons {
			pidStrs = append(pidStrs, strconv.Itoa(d.PID))
		}
		fmt.Fprintf(out, "Hub Daemons:        %d running (PIDs: %s)\n", len(st.Daemons), strings.Join(pidStrs, ", "))
		return nil
	}

	fmt.Fprintf(out, "Hub Daemons:        %d running\n", len(st.Daemons))
	tbl := terminal.NewTable("  PID", "HTTPS", "HTTP", "VERSION", "DESCRIPTOR")
	for _, d := range st.Daemons {
		ver := d.LSVersion
		if ver == "" {
			ver = "-"
		}
		tbl.AddRow(
			fmt.Sprintf("  %d", d.PID),
			strconv.Itoa(d.HTTPSPort),
			strconv.Itoa(d.HTTPPort),
			ver,
			d.FilePath,
		)
	}
	return tbl.Render(out)
}

func newHubCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "hub",
		Short: "Manage Antigravity 2.0 Hub SSH GUI OAuth profiles",
		Long: `Manage Antigravity 2.0 Hub SSH GUI OAuth profiles (~/.gemini/jetski-standalone-oauth-token)
and coordinate graceful Hub language_server restarts while sharing the agy profile vault.`,
		PersistentPreRunE: func(c *cobra.Command, args []string) error {
			if c.Name() == "_restart-pids" {
				return nil
			}
			return requireAgyTool()
		},
	}

	cmd.AddCommand(newHubSwitchCmd())
	cmd.AddCommand(newHubNextCmd())
	cmd.AddCommand(newHubSaveCmd())
	cmd.AddCommand(newHubWhoamiCmd())
	cmd.AddCommand(newHubStatusCmd())
	cmd.AddCommand(newHubRestartCmd())
	cmd.AddCommand(newHubRestartPIDsCmd())

	return cmd
}

func newHubSwitchCmd() *cobra.Command {
	var noRestart bool

	cmd := &cobra.Command{
		Use:   "switch <name>",
		Short: "Switch the Hub SSH GUI active profile",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			store := GetStorage()

			res, err := hub.Switch(cmdContext(cmd), store, name, hub.RestartOptions{NoRestart: noRestart})
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				if err := writeJSON(out, res); err != nil {
					return err
				}
				printHubRestartSummary(out, cmd.ErrOrStderr(), res.Restart, noRestart)
				return nil
			}

			fmt.Fprintf(out, "✓ Switched Hub profile to '%s'\n", res.Profile)
			if res.Refreshed {
				fmt.Fprintln(out, "  • Refreshed expired OAuth token before switching")
			}
			printHubRestartSummary(out, cmd.ErrOrStderr(), res.Restart, noRestart)
			return nil
		},
	}

	cmd.Flags().BoolVar(&noRestart, "no-restart", false, "Skip restarting running Hub language_server daemons")
	return cmd
}

func newHubNextCmd() *cobra.Command {
	var (
		reason    string
		cooldown  int
		noRestart bool
	)

	cmd := &cobra.Command{
		Use:   "next",
		Short: "Rotate the Hub SSH GUI to the next eligible standby profile",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()

			res, err := hub.RotateNext(cmdContext(cmd), store, reason, cooldown, hub.RestartOptions{NoRestart: noRestart})
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				if err := writeJSON(out, res); err != nil {
					return err
				}
				printHubRestartSummary(out, cmd.ErrOrStderr(), res.Restart, noRestart)
				return nil
			}

			if !res.Success {
				fmt.Fprintf(out, "✗ Hub rotation failed: %s\n", res.Reason)
				return nil
			}

			from := "none"
			if res.FromAccount != nil {
				from = *res.FromAccount
			}
			to := "none"
			if res.ToAccount != nil {
				to = *res.ToAccount
			}
			appliedCooldown := cooldown
			if appliedCooldown <= 0 {
				appliedCooldown = config.DefaultCooldownMinutes
			}

			fmt.Fprintf(out, "✓ Rotated Hub profile: %s -> %s (cooldown: %dm)\n", from, to, appliedCooldown)
			if res.Refreshed {
				fmt.Fprintln(out, "  • Refreshed expired OAuth token before rotating")
			}
			printHubRestartSummary(out, cmd.ErrOrStderr(), res.Restart, noRestart)
			return nil
		},
	}

	cmd.Flags().StringVar(&reason, "reason", "manual rotation", "Reason for Hub failover rotation")
	cmd.Flags().IntVar(&cooldown, "cooldown", config.DefaultCooldownMinutes, "Cooldown minutes for rotated Hub account")
	cmd.Flags().BoolVar(&noRestart, "no-restart", false, "Skip restarting running Hub language_server daemons")
	return cmd
}

func newHubSaveCmd() *cobra.Command {
	var (
		email    string
		project  string
		planType string
	)

	cmd := &cobra.Command{
		Use:   "save <name>",
		Short: "Save the current Hub live OAuth token as a named profile",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			store := GetStorage()

			var emailPtr, projPtr *string
			if email != "" {
				emailPtr = &email
			}
			if project != "" {
				projPtr = &project
			}

			acct, err := hub.Save(store, name, emailPtr, projPtr, planType)
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				return writeJSON(out, acct)
			}

			fmt.Fprintf(out, "✓ Saved Hub profile '%s' successfully\n", name)
			return nil
		},
	}

	cmd.Flags().StringVar(&email, "email", "", "Account email address (auto-detected from id_token_email if omitted)")
	cmd.Flags().StringVar(&project, "project", "", "Cloud companion project ID")
	cmd.Flags().StringVar(&planType, "plan", "STANDARD", "Plan type (e.g. STANDARD, PRO)")
	return cmd
}

func newHubWhoamiCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "whoami",
		Short: "Display Hub active profile, sync status, and running Hub daemons",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			st, err := hub.Status(store)
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				if err := writeJSON(out, st); err != nil {
					return err
				}
				emitHubSyncStatusWarning(cmd.ErrOrStderr(), st)
				return nil
			}

			return renderHubStatus(out, st, false)
		},
	}
}

func newHubStatusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Inspect detailed Hub profile, token sync, and language_server daemon status",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			st, err := hub.Status(store)
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				if err := writeJSON(out, st); err != nil {
					return err
				}
				emitHubSyncStatusWarning(cmd.ErrOrStderr(), st)
				return nil
			}

			return renderHubStatus(out, st, true)
		},
	}
}

func newHubRestartCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "restart",
		Short: "Restart running Hub language_server daemons cleanly",
		RunE: func(cmd *cobra.Command, args []string) error {
			res, err := hub.RestartDaemons(cmdContext(cmd), config.HubDaemonDir(""), hub.RestartOptions{})
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				if err := writeJSON(out, res); err != nil {
					return err
				}
				printHubRestartSummary(out, cmd.ErrOrStderr(), res, false)
				return nil
			}

			if res != nil && res.Deferred {
				fmt.Fprintf(out, "✓ Scheduled deferred restart for Hub daemon PID(s): %v\n", res.DeferredPIDs)
				return nil
			}
			if res == nil || len(res.StoppedPIDs) == 0 {
				fmt.Fprintln(out, "• No running Hub language_server daemons found")
				return nil
			}

			fmt.Fprintf(out, "✓ Restarted Hub daemon(s): stopped %v\n", res.StoppedPIDs)
			if len(res.RespawnedPIDs) > 0 {
				fmt.Fprintf(out, "  • Respawned Hub daemon PID(s): %v\n", res.RespawnedPIDs)
			}
			if res.NeedsReconnect {
				fmt.Fprintf(out, "  • Note: %s\n", hubReconnectNote)
			}
			return nil
		},
	}
}

func newHubRestartPIDsCmd() *cobra.Command {
	var delay time.Duration

	cmd := &cobra.Command{
		Use:    "_restart-pids <pid...>",
		Short:  "Internal helper to restart Hub daemon PIDs after a delay",
		Hidden: true,
		Args:   cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			pids := make([]int, 0, len(args))
			for _, arg := range args {
				pid, err := strconv.Atoi(arg)
				if err != nil || pid <= 0 {
					return fmt.Errorf("invalid PID %q", arg)
				}
				pids = append(pids, pid)
			}

			ctx := cmdContext(cmd)
			if delay > 0 {
				timer := time.NewTimer(delay)
				select {
				case <-ctx.Done():
					timer.Stop()
					return ctx.Err()
				case <-timer.C:
				}
			}

			_, err := hub.RestartPIDs(ctx, pids, config.HubDaemonDir(""), hub.RestartOptions{})
			return err
		},
	}

	cmd.Flags().DurationVar(&delay, "delay", 300*time.Millisecond, "Delay before signaling target PIDs")
	return cmd
}
