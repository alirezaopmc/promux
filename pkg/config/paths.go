package config

import (
	"bufio"
	"os"
	"path/filepath"
	"regexp"
)

func PromuxHome() string {
	if h := os.Getenv("PROMUX_HOME"); h != "" {
		return h
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ".promux"
	}
	return filepath.Join(home, ".promux")
}

func GeminiHome() string {
	if h := os.Getenv("PROMUX_GEMINI_HOME"); h != "" {
		return h
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ".gemini/antigravity-cli"
	}
	return filepath.Join(home, ".gemini", "antigravity-cli")
}

func ToolHome(tool string) string {
	return filepath.Join(PromuxHome(), "tools", tool)
}

func AccountsDir(tool string) string {
	return filepath.Join(ToolHome(tool), "accounts")
}

func StateFile(tool string) string {
	return filepath.Join(ToolHome(tool), "state.json")
}

func LockFile(tool string) string {
	return filepath.Join(ToolHome(tool), "manager.lock")
}

func CacheDir(tool string) string {
	return filepath.Join(ToolHome(tool), "cache")
}

func LiveTokenFile(geminiHome string) string {
	if geminiHome == "" {
		geminiHome = GeminiHome()
	}
	return filepath.Join(geminiHome, "antigravity-oauth-token")
}

func CLILogFile(geminiHome string) string {
	if geminiHome == "" {
		geminiHome = GeminiHome()
	}
	return filepath.Join(geminiHome, "cli.log")
}

func LogDir(geminiHome string) string {
	if geminiHome == "" {
		geminiHome = GeminiHome()
	}
	return filepath.Join(geminiHome, "log")
}

var urlRegex = regexp.MustCompile(`https://([a-zA-Z0-9.-]*cloudcode-pa\.googleapis\.com)`)

func DetectCodeAssistURL(geminiHome string) string {
	if u := os.Getenv("PROMUX_CODE_ASSIST_URL"); u != "" {
		return u
	}
	logFile := CLILogFile(geminiHome)
	f, err := os.Open(logFile)
	if err != nil {
		return CodeAssistBaseURL
	}
	defer f.Close()

	// Read last 100 lines
	var lines []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
		if len(lines) > 100 {
			lines = lines[1:]
		}
	}

	for i := len(lines) - 1; i >= 0; i-- {
		matches := urlRegex.FindStringSubmatch(lines[i])
		if len(matches) > 1 {
			return "https://" + matches[1]
		}
	}

	return CodeAssistBaseURL
}
