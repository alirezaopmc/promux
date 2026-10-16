//go:build !windows

package runner

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
	"time"

	"github.com/creack/pty"
	"golang.org/x/term"

	"github.com/alirezaopmc/promux/pkg/watcher"
)

func (r *Runner) Exec(ctx context.Context, name string, args []string) error {
	cmd := exec.CommandContext(ctx, name, args...)

	ptmx, err := pty.Start(cmd)
	if err != nil {
		return fmt.Errorf("failed to start command in PTY: %w", err)
	}
	defer func() { _ = ptmx.Close() }()

	// Handle terminal resizing
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, syscall.SIGWINCH)
	go func() {
		for range ch {
			if err := pty.InheritSize(os.Stdin, ptmx); err != nil {
				_ = err
			}
		}
	}()
	ch <- syscall.SIGWINCH // Initial resize
	defer func() {
		signal.Stop(ch)
		close(ch)
	}()

	// Set host terminal into raw mode if stdin is a terminal
	if term.IsTerminal(int(os.Stdin.Fd())) {
		oldState, err := term.MakeRaw(int(os.Stdin.Fd()))
		if err == nil {
			defer func() { _ = term.Restore(int(os.Stdin.Fd()), oldState) }()
		}
	}

	// Stdin -> PTY
	go func() {
		_, _ = io.Copy(ptmx, os.Stdin)
	}()

	scanner := NewOutputScanner(func(match watcher.LogMatch) {
		if r.Failover != nil {
			mins := watcher.ParseResetMinutes(match.ResetHint)
			res, err := r.Failover.RotateNext("runner: "+match.Pattern, mins)
			if err == nil && res.Success && r.AutoContinue {
				// Inject continue after delay
				go func() {
					time.Sleep(r.Delay)
					_, _ = ptmx.Write([]byte("continue\r\n"))
				}()
			}
		}
	})

	// PTY -> Stdout & Scanner
	multiWriter := io.MultiWriter(os.Stdout, scanner)
	_, _ = io.Copy(multiWriter, ptmx)

	return cmd.Wait()
}
