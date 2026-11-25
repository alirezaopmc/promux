package quota

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

var (
	codePattern  = regexp.MustCompile(`\(#\d+\)`)
	htmlTitleRe  = regexp.MustCompile(`(?i)<title>(.*?)</title>`)
	httpPrefixRe = regexp.MustCompile(`^HTTP\s+(\d+)`)
)

// APIError represents a structured error returned by the Google Cloud Code Assist API or OAuth server.
type APIError struct {
	StatusCode int    `json:"status_code"`
	URL        string `json:"url"`
	Status     string `json:"status,omitempty"`  // e.g. "PERMISSION_DENIED"
	Message    string `json:"message,omitempty"` // e.g. "You do not have a valid license of this product..."
	Reason     string `json:"reason,omitempty"`  // e.g. "SUBSCRIPTION_REQUIRED"
	RawBody    string `json:"raw_body,omitempty"`
}

func (e *APIError) Error() string {
	cleanMsg := CleanErrorMessage(e.Message)
	if e.Status != "" && cleanMsg != "" {
		return fmt.Sprintf("HTTP %d (%s): %s", e.StatusCode, e.Status, cleanMsg)
	}
	if e.Status != "" {
		return fmt.Sprintf("HTTP %d (%s)", e.StatusCode, e.Status)
	}
	if cleanMsg != "" {
		return fmt.Sprintf("HTTP %d: %s", e.StatusCode, cleanMsg)
	}
	if e.RawBody != "" {
		sanitized := strings.Join(strings.Fields(e.RawBody), " ")
		if len(sanitized) > 80 {
			sanitized = sanitized[:77] + "..."
		}
		return fmt.Sprintf("HTTP %d: %s", e.StatusCode, sanitized)
	}
	return fmt.Sprintf("HTTP %d", e.StatusCode)
}

// VerboseError returns the comprehensive un-truncated error with endpoint URL and raw payload.
func (e *APIError) VerboseError() string {
	if e.RawBody != "" {
		return fmt.Sprintf("HTTP %d from %s: %s", e.StatusCode, e.URL, e.RawBody)
	}
	if e.URL != "" {
		return fmt.Sprintf("HTTP %d from %s: %s", e.StatusCode, e.URL, e.Error())
	}
	return e.Error()
}

// ParseAPIError parses an HTTP response status code and body into an APIError.
func ParseAPIError(statusCode int, url string, body []byte) *APIError {
	apiErr := &APIError{
		StatusCode: statusCode,
		URL:        url,
		RawBody:    string(body),
	}

	trimmed := strings.TrimSpace(string(body))
	if trimmed == "" {
		return apiErr
	}

	// 1. Try parsing Google RPC Error JSON
	var rpcErr struct {
		Error struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
			Status  string `json:"status"`
			Details []struct {
				Reason string `json:"reason"`
			} `json:"details"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &rpcErr); err == nil && (rpcErr.Error.Message != "" || rpcErr.Error.Status != "") {
		apiErr.Status = rpcErr.Error.Status
		apiErr.Message = rpcErr.Error.Message
		for _, d := range rpcErr.Error.Details {
			if d.Reason != "" {
				apiErr.Reason = d.Reason
				break
			}
		}
		return apiErr
	}

	// 2. Try parsing OAuth error JSON (e.g. {"error": "invalid_grant", "error_description": "..."})
	var oauthErr struct {
		Error            string `json:"error"`
		ErrorDescription string `json:"error_description"`
	}
	if err := json.Unmarshal(body, &oauthErr); err == nil && (oauthErr.Error != "" || oauthErr.ErrorDescription != "") {
		apiErr.Status = oauthErr.Error
		apiErr.Message = oauthErr.ErrorDescription
		return apiErr
	}

	// 3. Try parsing generic JSON message (e.g. {"message": "..."})
	var genericJSON struct {
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &genericJSON); err == nil && genericJSON.Message != "" {
		apiErr.Message = genericJSON.Message
		return apiErr
	}

	// 4. Try HTML title extraction (e.g. 502 Bad Gateway)
	if strings.HasPrefix(trimmed, "<") {
		if m := htmlTitleRe.FindSubmatch(body); len(m) > 1 {
			apiErr.Message = strings.TrimSpace(string(m[1]))
			return apiErr
		}
	}

	return apiErr
}

// CleanErrorMessage extracts a concise single-line summary from an API error message.
// Boilerplate instructions and redundant sentences are stripped, while error codes (like #3501) are preserved.
func CleanErrorMessage(msg string) string {
	msg = strings.TrimSpace(msg)
	if msg == "" {
		return ""
	}

	// Normalize whitespace
	msg = strings.Join(strings.Fields(msg), " ")

	// Check if there is an error code like (#3501)
	codeMatch := codePattern.FindString(msg)

	// If there are multiple sentences separated by ". ", extract the first sentence
	if idx := strings.Index(msg, ". "); idx != -1 {
		firstSentence := strings.TrimSpace(msg[:idx])
		firstSentence = strings.TrimRight(firstSentence, ".")
		if codeMatch != "" && !strings.Contains(firstSentence, codeMatch) {
			return firstSentence + " " + codeMatch
		}
		return firstSentence
	}

	return strings.TrimRight(msg, ".")
}

// PrimeExecError captures the result of executing the agy child process during priming.
type PrimeExecError struct {
	Err    error
	Output string
}

func (e *PrimeExecError) Error() string {
	if errors.Is(e.Err, context.DeadlineExceeded) || (e.Err != nil && strings.Contains(e.Err.Error(), "context deadline exceeded")) {
		return "priming turn timed out after 20s"
	}
	if clean := CleanCLIOutput(e.Output); clean != "" {
		return clean
	}
	if e.Err != nil {
		return e.Err.Error()
	}
	return "priming command failed"
}

func (e *PrimeExecError) VerboseError() string {
	if e.Output != "" {
		return fmt.Sprintf("agy command failed: %v (output: %s)", e.Err, e.Output)
	}
	if e.Err != nil {
		return fmt.Sprintf("agy command failed: %v", e.Err)
	}
	return "agy command failed"
}

// CleanCLIOutput strips error prefixes and extracts the meaningful first line of CLI output.
func CleanCLIOutput(output string) string {
	output = strings.TrimSpace(output)
	if output == "" {
		return ""
	}

	lines := strings.Split(output, "\n")
	var targetLine string
	for _, l := range lines {
		l = strings.TrimSpace(l)
		if l != "" {
			targetLine = l
			break
		}
	}
	if targetLine == "" {
		targetLine = output
	}

	lower := strings.ToLower(targetLine)
	if strings.HasPrefix(lower, "error: ") {
		targetLine = strings.TrimSpace(targetLine[7:])
	} else if strings.HasPrefix(lower, "[error] ") {
		targetLine = strings.TrimSpace(targetLine[8:])
	}

	return strings.Join(strings.Fields(targetLine), " ")
}

// FormatQuotaError formats any quota query error cleanly for terminal display, respecting verbose mode.
func FormatQuotaError(err error, verbose bool) string {
	if err == nil {
		return ""
	}
	if verbose {
		var apiErr *APIError
		if errors.As(err, &apiErr) {
			return apiErr.VerboseError()
		}
		return err.Error()
	}

	var apiErr *APIError
	if errors.As(err, &apiErr) {
		return apiErr.Error()
	}

	errStr := err.Error()
	// Fallback: parse raw HTTP error strings that may have been passed unparsed
	if (strings.Contains(errStr, "HTTP ") || strings.Contains(errStr, "http ")) && strings.Contains(errStr, "{") {
		if clean := extractCleanHTTPErrorFromString(errStr); clean != "" {
			return clean
		}
	}

	// Collapse newlines for clean single-line output
	return strings.Join(strings.Fields(errStr), " ")
}

// FormatPrimeError formats a priming error cleanly for terminal display, respecting verbose mode.
func FormatPrimeError(err error, verbose bool) string {
	if err == nil {
		return ""
	}
	if verbose {
		var execErr *PrimeExecError
		if errors.As(err, &execErr) {
			return execErr.VerboseError()
		}
		return err.Error()
	}

	var execErr *PrimeExecError
	if errors.As(err, &execErr) {
		return execErr.Error()
	}

	errStr := err.Error()
	// If error string contains "(output: ...)"
	if strings.Contains(errStr, "(output: ") {
		if clean := extractCleanOutputFromString(errStr); clean != "" {
			return clean
		}
	}

	// Remove redundant prefixes
	errStr = strings.TrimPrefix(errStr, "headless priming turn failed: ")
	errStr = strings.TrimPrefix(errStr, "priming turn failed: ")

	return strings.Join(strings.Fields(errStr), " ")
}

func extractCleanOutputFromString(errStr string) string {
	idx := strings.Index(errStr, "(output: ")
	if idx != -1 {
		raw := errStr[idx+len("(output: "):]
		raw = strings.TrimSuffix(strings.TrimSpace(raw), ")")
		return CleanCLIOutput(raw)
	}
	return ""
}

func extractCleanHTTPErrorFromString(errStr string) string {
	var code int
	matches := httpPrefixRe.FindStringSubmatch(errStr)
	if len(matches) > 1 {
		_, _ = fmt.Sscanf(matches[1], "%d", &code)
	}
	if code == 0 {
		// Try finding any HTTP \d+
		re := regexp.MustCompile(`HTTP\s+(\d+)`)
		m := re.FindStringSubmatch(errStr)
		if len(m) > 1 {
			_, _ = fmt.Sscanf(m[1], "%d", &code)
		}
	}

	if jsonIdx := strings.Index(errStr, "{"); jsonIdx != -1 {
		body := []byte(errStr[jsonIdx:])
		parsed := ParseAPIError(code, "", body)
		if parsed.Status != "" || parsed.Message != "" {
			return parsed.Error()
		}
	}
	return ""
}
