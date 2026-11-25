package cmd

import (
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"github.com/fatih/color"
	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/failover"
	"github.com/alirezaopmc/promux/pkg/hub"
	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/terminal"
)

func newProfileCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "profile",
		Short: "Manage and inspect account profiles",
	}

	cmd.AddCommand(newListCmd())
	cmd.AddCommand(newSaveCmd())
	cmd.AddCommand(newSwitchCmd())
	cmd.AddCommand(newNextCmd())
	cmd.AddCommand(newWhoamiCmd())
	cmd.AddCommand(newRemoveCmd())

	return cmd
}

func newListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List all saved profiles",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			state, err := store.LoadState()
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				enc := json.NewEncoder(out)
				enc.SetIndent("", "  ")
				return enc.Encode(state)
			}

			if len(state.Accounts) == 0 {
				fmt.Fprintln(out, "No profiles saved. Use 'promux save <name>' to save current session.")
				return nil
			}

			accountNames := make([]string, 0, len(state.Accounts))
			for name := range state.Accounts {
				accountNames = append(accountNames, name)
			}
			sort.Strings(accountNames)

			now := time.Now().UTC()
			tbl := terminal.NewTable("  PROFILE", "STATUS", "LAST USED", "EMAIL", "PLAN")

			activeName := ""
			if state.Active != nil {
				activeName = *state.Active
			}
			hubActiveName := ""
			if state.HubActive != nil {
				hubActiveName = *state.HubActive
			}

			for _, name := range accountNames {
				acct := state.Accounts[name]
				prefix := " "
				st := acct.State(now)
				if name == activeName {
					prefix = color.GreenString("*")
					st = models.StateActive
				}

				statusStr := string(st)
				switch st {
				case models.StateActive, models.StateStandby:
					statusStr = color.GreenString(statusStr)
				case models.StateCooldown:
					statusStr = color.YellowString(statusStr)
				case models.StateDisabled:
					statusStr = color.RedString(statusStr)
				}

				lastUsed := "never"
				if acct.LastUsedAt != nil {
					lastUsed = acct.LastUsedAt.Format("2006-01-02 15:04")
				}

				email := "-"
				if acct.Email != nil && *acct.Email != "" {
					email = *acct.Email
				}

				hubTag := ""
				if hubActiveName != "" && name == hubActiveName {
					hubTag = " [hub]"
				}

				tbl.AddRow(fmt.Sprintf("%s %s%s", prefix, name, hubTag), statusStr, lastUsed, email, acct.PlanType)
			}
			return tbl.Render(out)
		},
	}
}

func newSaveCmd() *cobra.Command {
	var (
		email    string
		project  string
		planType string
	)

	cmd := &cobra.Command{
		Use:   "save <name>",
		Short: "Save current active credentials as a named profile",
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

			acct, err := store.SaveProfile(name, emailPtr, projPtr, planType)
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				return json.NewEncoder(out).Encode(acct)
			}

			fmt.Fprintf(out, "✓ Saved profile '%s' successfully\n", name)
			return nil
		},
	}

	cmd.Flags().StringVar(&email, "email", "", "Account email address")
	cmd.Flags().StringVar(&project, "project", "", "Cloud companion project ID")
	cmd.Flags().StringVar(&planType, "plan", "STANDARD", "Plan type (e.g. STANDARD, PRO)")

	return cmd
}

func newSwitchCmd() *cobra.Command {
	var (
		useHub    bool
		noRestart bool
	)

	cmd := &cobra.Command{
		Use:   "switch <name>",
		Short: "Switch active credentials to a named profile",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			if useHub {
				if err := requireAgyTool(); err != nil {
					return err
				}
			}

			store := GetStorage()
			var preRefreshed bool
			if useHub {
				var err error
				preRefreshed, err = ensureProfileTokenFresh(store, name)
				if err != nil {
					return err
				}
			}

			if err := store.SwitchProfile(name); err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if useHub {
				hubRes, err := hub.Switch(cmdContext(cmd), store, name, hub.RestartOptions{NoRestart: noRestart})
				if err != nil {
					return err
				}
				if preRefreshed {
					hubRes.Refreshed = true
				}

				if JSONOut {
					if err := writeJSON(out, map[string]any{
						"status":     "switched",
						"active":     name,
						"hub_active": hubRes.Profile,
						"hub":        hubRes,
					}); err != nil {
						return err
					}
					printHubRestartSummary(out, cmd.ErrOrStderr(), hubRes.Restart, noRestart)
					return nil
				}

				fmt.Fprintf(out, "✓ Switched active profile to '%s'\n", name)
				fmt.Fprintf(out, "✓ Switched Hub profile to '%s'\n", hubRes.Profile)
				if hubRes.Refreshed {
					fmt.Fprintln(out, "  • Refreshed expired OAuth token before switching")
				}
				printHubRestartSummary(out, cmd.ErrOrStderr(), hubRes.Restart, noRestart)
				return nil
			}

			if JSONOut {
				return json.NewEncoder(out).Encode(map[string]any{"status": "switched", "active": name})
			}

			fmt.Fprintf(out, "✓ Switched active profile to '%s'\n", name)
			return nil
		},
	}

	cmd.Flags().BoolVar(&useHub, "hub", false, "Also switch the Antigravity Hub SSH GUI profile")
	cmd.Flags().BoolVar(&noRestart, "no-restart", false, "Skip restarting running Hub language_server daemons when --hub is set")
	return cmd
}

func newNextCmd() *cobra.Command {
	var (
		reason    string
		cooldown  int
		useHub    bool
		noRestart bool
	)

	cmd := &cobra.Command{
		Use:   "next",
		Short: "Rotate to the next eligible standby profile",
		RunE: func(cmd *cobra.Command, args []string) error {
			if useHub {
				if err := requireAgyTool(); err != nil {
					return err
				}
			}

			store := GetStorage()
			fo := failover.New(store)

			res, err := fo.RotateNext(reason, cooldown)
			if err != nil {
				return err
			}

			var hubRes *hub.SwitchResult
			if useHub && res.Success && res.ToAccount != nil {
				hubRes, err = hub.Switch(cmdContext(cmd), store, *res.ToAccount, hub.RestartOptions{NoRestart: noRestart})
				if err != nil {
					return err
				}
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				if useHub {
					payload := struct {
						*models.RotationResult
						Hub *hub.SwitchResult `json:"hub,omitempty"`
					}{
						RotationResult: res,
						Hub:            hubRes,
					}
					if err := writeJSON(out, payload); err != nil {
						return err
					}
					if hubRes != nil {
						printHubRestartSummary(out, cmd.ErrOrStderr(), hubRes.Restart, noRestart)
					}
					return nil
				}
				return json.NewEncoder(out).Encode(res)
			}

			if !res.Success {
				fmt.Fprintf(out, "✗ Rotation failed: %s\n", res.Reason)
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

			fmt.Fprintf(out, "✓ Rotated profile: %s -> %s (cooldown: %dm)\n", from, to, cooldown)
			if useHub && hubRes != nil {
				fmt.Fprintf(out, "✓ Synced Hub profile to '%s'\n", hubRes.Profile)
				if hubRes.Refreshed {
					fmt.Fprintln(out, "  • Refreshed expired OAuth token before switching")
				}
				printHubRestartSummary(out, cmd.ErrOrStderr(), hubRes.Restart, noRestart)
			}
			return nil
		},
	}

	cmd.Flags().StringVar(&reason, "reason", "manual rotation", "Reason for failover rotation")
	cmd.Flags().IntVar(&cooldown, "cooldown", 60, "Cooldown minutes for rotated account")
	cmd.Flags().BoolVar(&useHub, "hub", false, "Also switch the Antigravity Hub SSH GUI profile")
	cmd.Flags().BoolVar(&noRestart, "no-restart", false, "Skip restarting running Hub language_server daemons when --hub is set")

	return cmd
}

func newWhoamiCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "whoami",
		Short: "Display active profile information",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			acct, err := store.GetActiveProfile()
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if acct == nil {
				if JSONOut {
					return json.NewEncoder(out).Encode(map[string]any{"active": nil})
				}
				fmt.Fprintln(out, "No active profile set.")
				return nil
			}

			if JSONOut {
				return json.NewEncoder(out).Encode(acct)
			}

			email := "-"
			if acct.Email != nil && *acct.Email != "" {
				email = *acct.Email
			}

			fmt.Fprintf(out, "Active Profile: %s\n", color.GreenString(acct.Name))
			fmt.Fprintf(out, "Email:          %s\n", email)
			fmt.Fprintf(out, "Plan:           %s\n", acct.PlanType)
			return nil
		},
	}
}

func newRemoveCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "remove <name>",
		Short: "Delete a saved profile from the vault",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			store := GetStorage()

			if err := store.RemoveProfile(name); err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				return json.NewEncoder(out).Encode(map[string]any{"status": "removed", "account": name})
			}

			fmt.Fprintf(out, "✓ Removed profile '%s'\n", name)
			return nil
		},
	}
}
