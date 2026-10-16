package runner

import (
	"context"
	"time"

	"github.com/alirezaopmc/promux/pkg/failover"
)

type Runner struct {
	Failover     *failover.FailoverEngine
	AutoContinue bool
	Delay        time.Duration
}

func New(fo *failover.FailoverEngine) *Runner {
	return &Runner{
		Failover:     fo,
		AutoContinue: true,
		Delay:        500 * time.Millisecond,
	}
}

type RunnerEngine interface {
	Exec(ctx context.Context, name string, args []string) error
}
