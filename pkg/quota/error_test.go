package quota

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestAPIError_Formatting_GoogleRPC(t *testing.T) {
	// The exact HTTP 403 error reported by the user
	rawJSON := `{
  "error": {
    "code": 403,
    "message": "You do not have a valid license of this product. Please contact your administrator to request a license. If you are not an enterprise user and believe you are receiving this message as an error, please try using the latest version and logging in again. (#3501)",
    "status": "PERMISSION_DENIED",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "SUBSCRIPTION_REQUIRED",
        "domain": "cloudaicompanion.googleapis.com",
        "metadata": {
          "uiMessage": "true",
          "error_number": "1001"
        }
      },
      {
        "@type": "type.googleapis.com/google.rpc.Help",
        "links": [
          {
            "description": "Learn more",
            "url": "https://cloud.google.com/gemini/docs/codeassist/request-license"
          }
        ]
      }
    ]
  }
}`
	u := "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
	apiErr := ParseAPIError(403, u, []byte(rawJSON))

	if apiErr.StatusCode != 403 {
		t.Fatalf("expected status code 403, got %d", apiErr.StatusCode)
	}
	if apiErr.Status != "PERMISSION_DENIED" {
		t.Fatalf("expected status PERMISSION_DENIED, got %s", apiErr.Status)
	}
	if apiErr.Reason != "SUBSCRIPTION_REQUIRED" {
		t.Fatalf("expected reason SUBSCRIPTION_REQUIRED, got %s", apiErr.Reason)
	}

	// Clean error test
	clean := apiErr.Error()
	expectedClean := "HTTP 403 (PERMISSION_DENIED): You do not have a valid license of this product (#3501)"
	if clean != expectedClean {
		t.Fatalf("expected clean error: %q, got: %q", expectedClean, clean)
	}

	// Verbose error test
	verbose := apiErr.VerboseError()
	if !strings.Contains(verbose, "HTTP 403 from https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary:") {
		t.Fatalf("expected verbose to contain URL header, got: %s", verbose)
	}
	if !strings.Contains(verbose, "SUBSCRIPTION_REQUIRED") {
		t.Fatalf("expected verbose to contain full raw body, got: %s", verbose)
	}
}

func TestAPIError_Formatting_ResourceExhausted(t *testing.T) {
	rawJSON := `{
  "error": {
    "code": 429,
    "message": "Quota exceeded for quota metric 'Queries' and limit 'Queries per minute'",
    "status": "RESOURCE_EXHAUSTED"
  }
}`
	apiErr := ParseAPIError(429, "https://example.com", []byte(rawJSON))
	clean := apiErr.Error()
	expected := "HTTP 429 (RESOURCE_EXHAUSTED): Quota exceeded for quota metric 'Queries' and limit 'Queries per minute'"
	if clean != expected {
		t.Fatalf("expected: %q, got: %q", expected, clean)
	}
}

func TestAPIError_Formatting_OAuthError(t *testing.T) {
	rawJSON := `{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}`
	apiErr := ParseAPIError(400, "https://oauth2.googleapis.com/token", []byte(rawJSON))
	clean := apiErr.Error()
	expected := "HTTP 400 (invalid_grant): Token has been expired or revoked"
	if clean != expected {
		t.Fatalf("expected: %q, got: %q", expected, clean)
	}
}

func TestAPIError_Formatting_HTMLFallback(t *testing.T) {
	htmlBody := `<html><head><title>502 Bad Gateway</title></head><body>502 Bad Gateway</body></html>`
	apiErr := ParseAPIError(502, "https://example.com", []byte(htmlBody))
	clean := apiErr.Error()
	expected := "HTTP 502: 502 Bad Gateway"
	if clean != expected {
		t.Fatalf("expected: %q, got: %q", expected, clean)
	}
}

func TestAPIError_Formatting_StatusOnly(t *testing.T) {
	rawJSON := `{"error": {"code": 404, "status": "NOT_FOUND"}}`
	apiErr := ParseAPIError(404, "https://example.com", []byte(rawJSON))
	clean := apiErr.Error()
	expected := "HTTP 404 (NOT_FOUND)"
	if clean != expected {
		t.Fatalf("expected: %q, got: %q", expected, clean)
	}
}

func TestFormatQuotaError_StringFallback(t *testing.T) {
	rawErrStr := `HTTP 403 from https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary: {
  "error": {
    "code": 403,
    "message": "You do not have a valid license of this product. Please contact your administrator. (#3501)",
    "status": "PERMISSION_DENIED"
  }
}`
	err := errors.New(rawErrStr)

	// Clean mode
	clean := FormatQuotaError(err, false)
	expectedClean := "HTTP 403 (PERMISSION_DENIED): You do not have a valid license of this product (#3501)"
	if clean != expectedClean {
		t.Fatalf("expected: %q, got: %q", expectedClean, clean)
	}

	// Verbose mode
	verbose := FormatQuotaError(err, true)
	if verbose != rawErrStr {
		t.Fatalf("expected verbose to match raw string, got: %s", verbose)
	}
}

func TestPrimeExecError_Formatting_EligibilityError(t *testing.T) {
	// The exact agy error reported by the user
	agyOutput := "error: Eligibility check failed: Your current account is not eligible for Antigravity, because it is not currently available in your location."
	execErr := &PrimeExecError{
		Err:    errors.New("exit status 1"),
		Output: agyOutput,
	}

	clean := execErr.Error()
	expectedClean := "Eligibility check failed: Your current account is not eligible for Antigravity, because it is not currently available in your location."
	if clean != expectedClean {
		t.Fatalf("expected: %q, got: %q", expectedClean, clean)
	}

	verbose := execErr.VerboseError()
	expectedVerbose := "agy command failed: exit status 1 (output: error: Eligibility check failed: Your current account is not eligible for Antigravity, because it is not currently available in your location.)"
	if verbose != expectedVerbose {
		t.Fatalf("expected verbose: %q, got: %q", expectedVerbose, verbose)
	}
}

func TestPrimeExecError_Formatting_Timeout(t *testing.T) {
	execErr := &PrimeExecError{
		Err:    context.DeadlineExceeded,
		Output: "",
	}

	clean := execErr.Error()
	expectedClean := "priming turn timed out after 20s"
	if clean != expectedClean {
		t.Fatalf("expected: %q, got: %q", expectedClean, clean)
	}
}

func TestFormatPrimeError_StringFallback(t *testing.T) {
	raw := "headless priming turn failed: agy command failed: exit status 1 (output: error: Eligibility check failed: Your current account is not eligible for Antigravity, because it is not currently available in your location.)"
	err := errors.New(raw)

	clean := FormatPrimeError(err, false)
	expectedClean := "Eligibility check failed: Your current account is not eligible for Antigravity, because it is not currently available in your location."
	if clean != expectedClean {
		t.Fatalf("expected: %q, got: %q", expectedClean, clean)
	}

	verbose := FormatPrimeError(err, true)
	if verbose != raw {
		t.Fatalf("expected verbose to equal raw, got: %s", verbose)
	}
}
