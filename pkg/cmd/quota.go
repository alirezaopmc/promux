package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/fatih/color"
	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/auth"
	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/quota"
	"github.com/alirezaopmc/promux/pkg/storage"
	"github.com/alirezaopmc/promux/pkg/terminal"
)

type ModelQuotaRecord struct {
	Remaining5h         float64 `json:"5h_remaining"`
	Reset5h             *string `json:"5h_reset"`
	Reset5hRelative     string  `json:"5h_reset_relative"`
	RemainingWeekly     float64 `json:"weekly_remaining"`
	ResetWeekly         *string `json:"weekly_reset"`
	ResetWeeklyRelative string  `json:"weekly_reset_relative"`
}

type AccountQuotaRecord struct {
	Account    string            `json:"account"`
	Active     bool              `json:"active"`
	ProjectID  string            `json:"project_id,omitempty"`
	Cached     bool              `json:"cached"`
	Gemini     *ModelQuotaRecord `json:"gemini,omitempty"`
	ThirdParty *ModelQuotaRecord `json:"third_party,omitempty"`
	Error      string            `json:"error,omitempty"`
}

func newQuotaCmd() *cobra.Command {
	var (
		accountName string
		allAccounts bool
		force       bool
		prime       bool
	)

	cmd := &cobra.Command{
		Use:   "quota [account]",
		Short: "Inspect Cloud Code Assist quota and reset countdowns",
		Example: `  # Check active profile quota
  promux quota

  # List all vaulted account quotas together in an overview table
  promux quota --all
  promux quota all

  # Check quota for a specific profile
  promux quota personal
  promux quota --account personal

  # Bypass local cache and query Google APIs live
  promux quota --all --refresh

  # Prime eligible profiles and display updated quota overview
  promux quota --all --prime

  # Export all quotas as machine-readable JSON
  promux quota --all --json`,
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			out := cmd.OutOrStdout()
			ctx := cmd.Context()
			if ctx == nil {
				ctx = context.Background()
			}

			if len(args) > 0 {
				if args[0] == "all" {
					allAccounts = true
				} else if accountName == "" {
					accountName = args[0]
				}
			}

			cache := quota.NewCache(store.Home)

			if allAccounts {
				if prime {
					results, err := primeAllEligibleFunc(ctx, store, cache, false)
					if err != nil {
						return err
					}
					if !JSONOut {
						printed := false
						for _, r := range results {
							if r.Primed {
								fmt.Fprintf(out, "⚡ Primed weekly quota for profile '%s'\n", r.Account)
								printed = true
							} else if r.Error != "" {
								fmt.Fprintf(out, "✗ Failed to prime profile '%s': %s\n", r.Account, r.Error)
								printed = true
							}
						}
						if printed {
							fmt.Fprintln(out)
						}
					}
				}
				return listAllQuotas(out, store, cache, force)
			}

			targetName := accountName
			if targetName == "" {
				active, err := store.GetActiveProfile()
				if err != nil {
					return err
				}
				if active == nil {
					return fmt.Errorf("no active profile set; use --account <name>, 'promux quota --all', or 'promux switch <name>'")
				}
				targetName = active.Name
			}

			if prime {
				res, err := primeAccountFunc(ctx, store, cache, targetName, false)
				if err != nil {
					if !JSONOut {
						fmt.Fprintf(out, "✗ Failed to prime profile '%s': %v\n\n", targetName, err)
					}
				} else if !JSONOut && res != nil && res.Primed {
					fmt.Fprintf(out, "⚡ Primed weekly quota for profile '%s'\n\n", targetName)
				}
			}

			qs, _, err := fetchAccountQuota(store, cache, targetName, force)
			if err != nil {
				return err
			}

			if JSONOut {
				return json.NewEncoder(out).Encode(qs)
			}

			printQuota(out, targetName, qs)
			return nil
		},
	}

	cmd.Flags().StringVarP(&accountName, "account", "a", "", "Target account profile (defaults to active)")
	cmd.Flags().BoolVarP(&allAccounts, "all", "A", false, "Inspect quota for all profiles in the vault")
	cmd.Flags().BoolVarP(&force, "refresh", "r", false, "Bypass cache and force live inspection")
	cmd.Flags().BoolVarP(&prime, "prime", "p", false, "Prime accounts that have reset weekly quota before displaying")

	return cmd
}

func fetchAccountQuota(store *storage.StorageEngine, cache *quota.QuotaCache, targetName string, force bool) (*models.QuotaSummary, string, error) {
	if !force {
		if cached, ok := cache.Get(targetName); ok {
			return cached, "", nil
		}
	}

	acct, err := store.GetProfile(targetName)
	if err != nil {
		return nil, "", err
	}

	state, _ := store.LoadState()
	isActive := state.Active != nil && *state.Active == targetName

	if isActive {
		auth.SafeSyncLiveToVault(store, targetName)
	}

	vaultToken := filepath.Join(store.AccountsDir, targetName, "antigravity-oauth-token")
	tokenData, err := auth.ReadTokenData(vaultToken)
	if err != nil {
		return nil, "", fmt.Errorf("failed to read token: %w", err)
	}

	accToken := auth.ExtractAccessToken(tokenData)
	if accToken == "" || auth.IsTokenExpired(tokenData, config.DefaultTokenExpiryBufferSeconds) {
		_, err := auth.RefreshAccountToken(store, targetName, true)
		if err != nil {
			return nil, "", fmt.Errorf("failed to refresh token: %w", err)
		}
		tokenData, _ = auth.ReadTokenData(vaultToken)
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
		return nil, projectID, fmt.Errorf("failed to fetch quota: %w", err)
	}

	_ = cache.Set(targetName, qs)
	return qs, projectID, nil
}

func listAllQuotas(out io.Writer, store *storage.StorageEngine, cache *quota.QuotaCache, force bool) error {
	state, err := store.LoadState()
	if err != nil {
		return err
	}

	if len(state.Accounts) == 0 {
		if JSONOut {
			return json.NewEncoder(out).Encode([]AccountQuotaRecord{})
		}
		fmt.Fprintln(out, "No profiles registered in vault.")
		return nil
	}

	accountNames := make([]string, 0, len(state.Accounts))
	for name := range state.Accounts {
		accountNames = append(accountNames, name)
	}
	sort.Strings(accountNames)

	type resultEntry struct {
		name      string
		isActive  bool
		qs        *models.QuotaSummary
		projectID string
		cached    bool
		err       error
	}

	results := make([]resultEntry, len(accountNames))
	var wg sync.WaitGroup

	for i, name := range accountNames {
		wg.Add(1)
		go func(idx int, acctName string) {
			defer wg.Done()
			isActive := state.Active != nil && *state.Active == acctName
			wasCached := false
			if !force {
				if _, ok := cache.Get(acctName); ok {
					wasCached = true
				}
			}
			qs, projID, err := fetchAccountQuota(store, cache, acctName, force)
			results[idx] = resultEntry{
				name:      acctName,
				isActive:  isActive,
				qs:        qs,
				projectID: projID,
				cached:    wasCached,
				err:       err,
			}
		}(i, name)
	}
	wg.Wait()

	now := time.Now().UTC()

	if JSONOut {
		records := make([]AccountQuotaRecord, len(results))
		for i, res := range results {
			rec := AccountQuotaRecord{
				Account:   res.name,
				Active:    res.isActive,
				ProjectID: res.projectID,
				Cached:    res.cached,
			}
			if res.err != nil {
				rec.Error = res.err.Error()
			} else if res.qs != nil {
				rec.Gemini = &ModelQuotaRecord{
					Remaining5h:         res.qs.Gemini5hRemaining,
					Reset5h:             res.qs.Gemini5hReset,
					Reset5hRelative:     formatResetRelative(res.qs.Gemini5hReset, now),
					RemainingWeekly:     res.qs.GeminiWeeklyRemaining,
					ResetWeekly:         res.qs.GeminiWeeklyReset,
					ResetWeeklyRelative: formatResetRelative(res.qs.GeminiWeeklyReset, now),
				}
				rec.ThirdParty = &ModelQuotaRecord{
					Remaining5h:         res.qs.ThirdParty5hRemaining,
					Reset5h:             res.qs.ThirdParty5hReset,
					Reset5hRelative:     formatResetRelative(res.qs.ThirdParty5hReset, now),
					RemainingWeekly:     res.qs.ThirdPartyWeeklyRemaining,
					ResetWeekly:         res.qs.ThirdPartyWeeklyReset,
					ResetWeeklyRelative: formatResetRelative(res.qs.ThirdPartyWeeklyReset, now),
				}
			}
			records[i] = rec
		}
		enc := json.NewEncoder(out)
		enc.SetIndent("", "  ")
		return enc.Encode(records)
	}

	tbl := terminal.NewTable("  PROFILE", "GEMINI (5H)", "GEMINI (WK)", "CLAUDE (5H)", "CLAUDE (WK)")

	for _, res := range results {
		prefix := " "
		if res.isActive {
			prefix = color.GreenString("*")
		}

		prefixName := fmt.Sprintf("%s %s", prefix, res.name)

		if res.err != nil {
			errStr := color.RedString("[ERROR]")
			tbl.AddRow(prefixName, errStr, "-", "-", "-")
			continue
		}

		g5 := formatOverviewCell(res.qs.Gemini5hRemaining, res.qs.Gemini5hReset, now)
		gw := formatOverviewCell(res.qs.GeminiWeeklyRemaining, res.qs.GeminiWeeklyReset, now)
		c5 := formatOverviewCell(res.qs.ThirdParty5hRemaining, res.qs.ThirdParty5hReset, now)
		cw := formatOverviewCell(res.qs.ThirdPartyWeeklyRemaining, res.qs.ThirdPartyWeeklyReset, now)

		tbl.AddRow(prefixName, g5, gw, c5, cw)
	}

	return tbl.Render(out)
}

func formatOverviewCell(fraction float64, resetStr *string, now time.Time) string {
	pctRaw := quota.FormatPercentage(fraction)
	pctFormatted := fmt.Sprintf("%6s", pctRaw)

	var pct string
	if fraction > 0.5 {
		pct = color.GreenString(pctFormatted)
	} else if fraction > 0.2 {
		pct = color.YellowString(pctFormatted)
	} else {
		pct = color.RedString(pctFormatted)
	}

	if resetStr != nil && *resetStr != "" {
		if t, ok := quota.ParseResetTimestamp(*resetStr); ok {
			countdown := quota.FormatResetDuration(t, now)
			if countdown != "" && countdown != "-" && countdown != "now" {
				return fmt.Sprintf("%s (%s)", pct, countdown)
			}
		}
	}

	return pct
}

func formatResetRelative(resetStr *string, now time.Time) string {
	if resetStr != nil && *resetStr != "" {
		if t, ok := quota.ParseResetTimestamp(*resetStr); ok {
			dur := quota.FormatResetDuration(t, now)
			if dur != "" && dur != "now" {
				return dur
			}
		}
	}
	return "-"
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
