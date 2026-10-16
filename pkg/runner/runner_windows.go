//go:build windows

package runner

import (
	"context"
	"io"
	"os"
	"os/exec"
	"time"

	"github.com/alirezaopmc/promux/pkg/watcher"
)

func (r *Runner) Exec(ctx context.Context, name string, args []string) error {
	cmd := exec.CommandContext(ctx, name, args...)

	stdinPipe, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	defer stdinPipe.Close()

	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}

	cmd.Stderr = os.Stderr

	if err := cmd.Start(); err != nil {
		return err
	}

	go func() {
		_, _ = io.Copy(stdinPipe, os.Stdin)
	}()

	scanner := NewOutputScanner(func(match watcher.LogMatch) {
		if r.Failover != nil {
			mins := watcher.ParseResetMinutes(match.ResetHint)
			res, err := r.Failover.RotateNext("runner: "+match.Pattern, mins)
			if err == nil && res.Success && r.AutoContinue {
				go func() {
					time.Sleep(r.Delay)
					_, _ = stdinPipe.Write([]byte("continue\r\n"))
				}()
			}
		}
	})

	multiWriter := io.MultiWriter(os.Stdout, scanner)
	_, _ = io.Copy(multiWriter, stdoutPipe)

	return cmd.Wait()
}
