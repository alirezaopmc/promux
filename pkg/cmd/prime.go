package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"github.com/fatih/color"
	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/quota"
)

var (
	primeAccountFunc     = quota.PrimeAccount
	primeAllEligibleFunc = quota.PrimeAllEligible
	queryQuotaFunc       = quota.QueryAccountQuota
)


func newPrimeCmd() *cobra.Command {
	var (
		force    bool
		dryRun   bool
		jsonFlag bool
	)

	cmd := &cobra.Command{
		Use:   "prime [account]",
		Short: "Prime weekly quota reset countdowns via minimal headless turns",
		Long: `Prime weekly quota reset countdowns by detecting accounts whose weekly quota has reset
(or has floating 100% capacity) and executing a minimal headless prompt turn under lock to fix the 7-day reset countdown.`,
		Example: `  # Prime all eligible profiles that have reset or have floating quota
  promux prime

  # Prime a specific vaulted account
  promux prime personal

  # Check which profiles are eligible without sending prompt tokens
  promux prime --dry-run

  # Force priming even if an active countdown is already ticking
  promux prime --force

  # Output results in machine-readable JSON
  promux prime --json`,
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			out := cmd.OutOrStdout()
			ctx := cmd.Context()
			if ctx == nil {
				ctx = context.Background()
			}

			isJSON := JSONOut || jsonFlag
			cache := quota.NewCache(store.Home)

			state, err := store.LoadState()
			if err != nil {
				return fmt.Errorf("failed to load state: %w", err)
			}

			var targetAccounts []string
			if len(args) > 0 && args[0] != "all" {
				target := args[0]
				if _, exists := state.Accounts[target]; !exists {
					return fmt.Errorf("profile '%s' not found in vault", target)
				}
				targetAccounts = []string{target}
			} else {
				if len(state.Accounts) == 0 {
					if isJSON {
						enc := json.NewEncoder(out)
						enc.SetIndent("", "  ")
						return enc.Encode([]quota.PrimeResult{})
					}
					fmt.Fprintln(out, "No profiles registered in vault.")
					return nil
				}

				targetAccounts = make([]string, 0, len(state.Accounts))
				for name := range state.Accounts {
					targetAccounts = append(targetAccounts, name)
				}
				sort.Strings(targetAccounts)
			}

			if !isJSON {
				if len(targetAccounts) > 1 {
					fmt.Fprintf(out, "Checking weekly quota status across %d profiles...\n", len(targetAccounts))
				} else {
					fmt.Fprintf(out, "Checking weekly quota status for profile '%s'...\n", targetAccounts[0])
				}
			}

			results := make([]quota.PrimeResult, 0, len(targetAccounts))
			primedCount := 0
			skippedCount := 0
			errorCount := 0
			now := time.Now().UTC()

			for _, name := range targetAccounts {
				var qs *models.QuotaSummary
				if cache != nil {
					qs, _ = cache.Get(name)
				}
				if qs == nil && !force {
					var qErr error
					qs, qErr = queryQuotaFunc(ctx, store, name)
					if qErr != nil {
						errorCount++
						res := quota.PrimeResult{
							Account: name,
							Primed:  false,
							Error:   qErr.Error(),
						}
						results = append(results, res)
						if !isJSON {
							fmt.Fprintf(out, "%s %s: failed to query quota: %v\n", color.RedString("✗"), name, qErr)
						}
						continue
					}
					if cache != nil && qs != nil {
						_ = cache.Set(name, qs)
					}
				}

				eligible, reason := quota.IsEligibleForPrime(qs, now, force)

				if dryRun {
					if eligible {
						primedCount++
						results = append(results, quota.PrimeResult{
							Account: name,
							Primed:  false,
							Reason:  reason,
						})
						if !isJSON {
							fmt.Fprintf(out, "%s %s: %s (would prime)\n", color.YellowString("⚡"), name, reason)
						}
					} else {
						skippedCount++
						results = append(results, quota.PrimeResult{
							Account: name,
							Primed:  false,
							Reason:  reason,
						})
						if !isJSON {
							fmt.Fprintf(out, "%s %s: %s - skipped\n", color.GreenString("✓"), name, reason)
						}
					}
					continue
				}

				// Live mode
				if !eligible {
					skippedCount++
					results = append(results, quota.PrimeResult{
						Account: name,
						Primed:  false,
						Reason:  reason,
					})
					if !isJSON {
						fmt.Fprintf(out, "%s %s: %s - skipped\n", color.GreenString("✓"), name, reason)
					}
					continue
				}

				if !isJSON {
					fmt.Fprintf(out, "%s %s: %s -> Priming via agy...\n", color.YellowString("⚡"), name, reason)
				}

				res, pErr := primeAccountFunc(ctx, store, cache, name, force)
				if pErr != nil {
					errorCount++
					results = append(results, quota.PrimeResult{
						Account: name,
						Primed:  false,
						Reason:  reason,
						Error:   pErr.Error(),
					})
					if !isJSON {
						fmt.Fprintf(out, "%s [%s] Priming failed: %v\n", color.RedString("✗"), name, pErr)
					}
				} else if res != nil && res.Primed {
					primedCount++
					results = append(results, *res)
					if !isJSON {
						if res.NewReset != nil && *res.NewReset != "" {
							rel := formatResetRelative(res.NewReset, time.Now().UTC())
							fmt.Fprintf(out, "   [%s] Token primed! New weekly reset: %s (%s)\n", name, *res.NewReset, rel)
						} else if res.Error != "" {
							fmt.Fprintf(out, "   [%s] Token primed! (warning: quota refresh delayed: %s)\n", name, res.Error)
						} else {
							fmt.Fprintf(out, "   [%s] Token primed!\n", name)
						}
					}
				} else {
					skippedCount++
					if res != nil {
						results = append(results, *res)
						if !isJSON {
							fmt.Fprintf(out, "%s %s: %s - skipped\n", color.GreenString("✓"), name, res.Reason)
						}
					}
				}
			}

			if isJSON {
				enc := json.NewEncoder(out)
				enc.SetIndent("", "  ")
				return enc.Encode(results)
			}

			if dryRun {
				fmt.Fprintf(out, "\nSummary: %s would be primed, %s (dry-run).\n",
					pluralize(primedCount, "account", "accounts"),
					pluralize(skippedCount, "account skipped", "accounts skipped"))
			} else if errorCount > 0 {
				fmt.Fprintf(out, "\nSummary: %s primed, %s, %s.\n",
					pluralize(primedCount, "account", "accounts"),
					pluralize(skippedCount, "account skipped", "accounts skipped"),
					pluralize(errorCount, "error", "errors"))
			} else {
				fmt.Fprintf(out, "\nSummary: %s primed, %s.\n",
					pluralize(primedCount, "account", "accounts"),
					pluralize(skippedCount, "account skipped", "accounts skipped"))
			}

			return nil
		},
	}

	cmd.Flags().BoolVarP(&force, "force", "f", false, "Force priming even if an active countdown is already ticking")
	cmd.Flags().BoolVarP(&dryRun, "dry-run", "n", false, "Check eligibility and list accounts without sending tokens")
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "Output results in JSON format")

	return cmd
}

func pluralize(count int, singular, plural string) string {
	if count == 1 {
		return fmt.Sprintf("1 %s", singular)
	}
	return fmt.Sprintf("%d %s", count, plural)
}
