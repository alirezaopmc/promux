package cmd

import (
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"github.com/fatih/color"
	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/failover"
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

				tbl.AddRow(fmt.Sprintf("%s %s", prefix, name), statusStr, lastUsed, email, acct.PlanType)
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
	return &cobra.Command{
		Use:   "switch <name>",
		Short: "Switch active credentials to a named profile",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			store := GetStorage()

			if err := store.SwitchProfile(name); err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
				return json.NewEncoder(out).Encode(map[string]any{"status": "switched", "active": name})
			}

			fmt.Fprintf(out, "✓ Switched active profile to '%s'\n", name)
			return nil
		},
	}
}

func newNextCmd() *cobra.Command {
	var (
		reason   string
		cooldown int
	)

	cmd := &cobra.Command{
		Use:   "next",
		Short: "Rotate to the next eligible standby profile",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			fo := failover.New(store)

			res, err := fo.RotateNext(reason, cooldown)
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if JSONOut {
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
			return nil
		},
	}

	cmd.Flags().StringVar(&reason, "reason", "manual rotation", "Reason for failover rotation")
	cmd.Flags().IntVar(&cooldown, "cooldown", 60, "Cooldown minutes for rotated account")

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
