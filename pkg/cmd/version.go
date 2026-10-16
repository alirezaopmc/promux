package cmd

import (
	"encoding/json"

	"github.com/spf13/cobra"
)

func newVersionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print the Promux CLI version",
		RunE: func(cmd *cobra.Command, args []string) error {
			if JSONOut {
				return json.NewEncoder(cmd.OutOrStdout()).Encode(map[string]string{"version": Version})
			}
			cmd.Printf("promux version %s\n", Version)
			return nil
		},
	}
}
