package cmd

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/failover"
	"github.com/alirezaopmc/promux/pkg/runner"
)

func newRunCmd() *cobra.Command {
	var noAutoContinue bool

	cmd := &cobra.Command{
		Use:                "run <command> [args...]",
		Short:              "Run a command in an autonomous PTY with automated quota failover & continue",
		Args:               cobra.MinimumNArgs(1),
		DisableFlagParsing: false,
		RunE: func(cmd *cobra.Command, args []string) error {
			prog := args[0]
			progArgs := args[1:]

			store := GetStorage()
			fo := failover.New(store)

			r := runner.New(fo)
			r.AutoContinue = !noAutoContinue

			ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
			defer cancel()

			return r.Exec(ctx, prog, progArgs)
		},
	}

	cmd.Flags().BoolVar(&noAutoContinue, "no-auto-continue", false, "Disable automated 'continue' injection upon quota failover")
	cmd.Flags().SetInterspersed(false) // Pass remaining flags to child command

	return cmd
}
