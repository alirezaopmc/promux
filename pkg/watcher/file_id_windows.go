//go:build windows

package watcher

import "os"

func getFileInode(fi os.FileInfo) uint64 {
	return 0
}
