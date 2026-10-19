package cmd

import (
	"bytes"
	"testing"
)

func TestWatchCmdAutoPrimeFlag(t *testing.T) {
	cmd := newWatchCmd()
	flag := cmd.Flags().Lookup("auto-prime")
	if flag == nil {
		t.Fatal("expected --auto-prime flag to be registered on watch command")
	}
	if flag.DefValue != "false" {
		t.Errorf("expected --auto-prime default value to be 'false', got %s", flag.DefValue)
	}

	buf := new(bytes.Buffer)
	cmd.SetOut(buf)
	cmd.SetArgs([]string{"--help"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("unexpected error on help: %v", err)
	}
	if !bytes.Contains(buf.Bytes(), []byte("--auto-prime")) {
		t.Errorf("expected help text to mention --auto-prime, got: %s", buf.String())
	}
}
