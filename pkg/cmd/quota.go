package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"path/filepath"
	"time"

	"github.com/fatih/color"
	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/auth"
	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/quota"
)

func newQuotaCmd() *cobra.Command {
	var (
		accountName string
		force       bool
	)

	cmd := &cobra.Command{
		Use:   "quota",
		Short: "Inspect Cloud Code Assist quota and reset countdowns",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			out := cmd.OutOrStdout()

			targetName := accountName
			if targetName == "" {
				active, err := store.GetActiveProfile()
				if err != nil {
					return err
				}
				if active == nil {
					return fmt.Errorf("no active profile set; use --account <name> or 'promux switch <name>'")
				}
				targetName = active.Name
			}

			cache := quota.NewCache(store.Home)
			if !force {
				if cached, ok := cache.Get(targetName); ok {
					if JSONOut {
						return json.NewEncoder(out).Encode(cached)
					}
					printQuota(out, targetName, cached)
					return nil
				}
			}

			// Read token
			tokenPath := store.LiveToken
			acct, err := store.GetProfile(targetName)
			if err != nil {
				return err
			}

			state, _ := store.LoadState()
			if state.Active == nil || *state.Active != targetName {
				tokenPath = filepath.Join(store.AccountsDir, targetName, "antigravity-oauth-token")
			}

			tokenData, err := auth.ReadTokenData(tokenPath)
			if err != nil {
				return fmt.Errorf("failed to read token: %w", err)
			}

			accToken := auth.ExtractAccessToken(tokenData)
			if accToken == "" || auth.IsTokenExpired(tokenData, config.DefaultTokenExpiryBufferSeconds) {
				_, err := auth.RefreshAccountToken(store, targetName, true)
				if err != nil {
					return fmt.Errorf("failed to refresh token: %w", err)
				}
				tokenData, _ = auth.ReadTokenData(tokenPath)
				accToken = auth.ExtractAccessToken(tokenData)
			}

			client := quota.NewClient(accToken, "", config.GetHTTPTimeout())
			projectID := ""
			if acct.ProjectID != nil {
				projectID = *acct.ProjectID
			}

			if projectID == "" {
				meta, err := client.LoadMetadata()
				if err == nil && meta.ProjectID != "" {
					projectID = meta.ProjectID
				}
			}

			qs, err := client.GetQuota(projectID)
			if err != nil {
				return fmt.Errorf("failed to fetch quota: %w", err)
			}

			_ = cache.Set(targetName, qs)

			if JSONOut {
				return json.NewEncoder(out).Encode(qs)
			}

			printQuota(out, targetName, qs)
			return nil
		},
	}

	cmd.Flags().StringVarP(&accountName, "account", "a", "", "Target account profile (defaults to active)")
	cmd.Flags().BoolVarP(&force, "refresh", "r", false, "Bypass cache and force live inspection")

	return cmd
}

func printQuota(w io.Writer, name string, qs *models.QuotaSummary) {
	fmt.Fprintf(w, "Quota Summary for Profile: %s\n\n", color.CyanString(name))

	now := time.Now().UTC()

	formatWindow := func(fraction float64, resetStr *string) string {
		pct := quota.FormatPercentage(fraction)
		if fraction > 0.5 {
			pct = color.GreenString(pct)
		} else if fraction > 0.2 {
			pct = color.YellowString(pct)
		} else {
			pct = color.RedString(pct)
		}

		if resetStr != nil && *resetStr != "" {
			if t, ok := quota.ParseResetTimestamp(*resetStr); ok {
				return fmt.Sprintf("%s (resets in %s)", pct, quota.FormatResetDuration(t, now))
			}
		}
		return pct
	}

	fmt.Fprintln(w, "Gemini Models:")
	fmt.Fprintf(w, "  • 5-Hour Window: %s\n", formatWindow(qs.Gemini5hRemaining, qs.Gemini5hReset))
	fmt.Fprintf(w, "  • Weekly Window: %s\n\n", formatWindow(qs.GeminiWeeklyRemaining, qs.GeminiWeeklyReset))

	fmt.Fprintln(w, "Third-Party Models:")
	fmt.Fprintf(w, "  • 5-Hour Window: %s\n", formatWindow(qs.ThirdParty5hRemaining, qs.ThirdParty5hReset))
	fmt.Fprintf(w, "  • Weekly Window: %s\n", formatWindow(qs.ThirdPartyWeeklyRemaining, qs.ThirdPartyWeeklyReset))
}

func newRefreshCmd() *cobra.Command {
	var force bool

	cmd := &cobra.Command{
		Use:   "refresh [account]",
		Short: "Force an OAuth token refresh for the active or named profile",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			targetName := ""
			if len(args) > 0 {
				targetName = args[0]
			} else {
				active, err := store.GetActiveProfile()
				if err != nil {
					return err
				}
				if active == nil {
					return fmt.Errorf("no active profile set")
				}
				targetName = active.Name
			}

			refreshed, err := auth.RefreshAccountToken(store, targetName, force)
			if err != nil {
				return err
			}

			out := cmd.OutOrStdout()
			if refreshed {
				fmt.Fprintf(out, "✓ Successfully refreshed OAuth credentials for '%s'\n", targetName)
			} else {
				fmt.Fprintf(out, "• Credentials for '%s' are still valid (use --force to renew now)\n", targetName)
			}
			return nil
		},
	}

	cmd.Flags().BoolVarP(&force, "force", "f", false, "Force renewal even if not expired")

	return cmd
}
