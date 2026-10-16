//go:build !windows

package watcher

import (
	"os"
	"syscall"
)

func getFileInode(fi os.FileInfo) uint64 {
	if stat, ok := fi.Sys().(*syscall.Stat_t); ok {
		return stat.Ino
	}
	return 0
}
