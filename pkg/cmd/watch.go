package cmd

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/fatih/color"
	"github.com/spf13/cobra"

	"github.com/alirezaopmc/promux/pkg/failover"
	"github.com/alirezaopmc/promux/pkg/watcher"
)

func newWatchCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "watch",
		Short: "Start the real-time background log watcher and failover daemon",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := GetStorage()
			fo := failover.New(store)
			w := watcher.NewLogWatcher(store, fo, store.GeminiHome)

			ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
			defer stop()

			fmt.Println(color.GreenString("Starting Promux log watcher daemon..."))
			fmt.Printf("Watching logs in: %s\n", store.GeminiHome)
			fmt.Println("Press Ctrl+C to exit.")

			return w.RunForever(ctx, func(m watcher.LogMatch) {
				fmt.Printf("[%s] Quota wall detected: %s (reset hint: %s)\n",
					timeNow(), color.YellowString(m.Pattern), m.ResetHint)
			})
		},
	}
}

func timeNow() string {
	return time.Now().Format("15:04:05")
}
