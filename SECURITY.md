# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Security Model & Token Handling

Promux manages authentication tokens and Cloud Code Assist quota hot-swapping. Its security architecture follows strict local least-privilege standards:
- **Local Isolation**: Account tokens are stored locally under `~/.promux/accounts/<name>/antigravity-oauth-token` with POSIX `0600` permissions (read/write only by owner).
- **Atomic Operations**: All file replacements utilize temporary files and atomic `os.replace` to prevent partial reads or torn token states.
- **Advisory Locking**: All vault operations use `fcntl.flock` to guarantee serialized access.
- **Client Credentials**: The default OAuth client credentials embedded in Promux correspond to the public desktop client registration for Google Cloud Code / Antigravity CLI. For custom environments or enterprise domains, client credentials can be overridden via `PROMUX_OAUTH_CLIENT_ID` and `PROMUX_OAUTH_CLIENT_SECRET`.

## Reporting a Vulnerability

If you discover a security vulnerability in Promux, please **do not report it via a public GitHub issue**.

Instead, report it privately:
1. Open a private vulnerability report via GitHub's [Security Advisories](https://github.com/alirezaopmc/promux/security/advisories) feature on the repository.
2. Alternatively, contact the maintainer directly.

Please include:
- A description of the issue and potential impact
- Step-by-step instructions or proof-of-concept to reproduce the vulnerability
- Any proposed remediation or mitigation

You will receive an acknowledgment within 48 hours, followed by updates as the issue is investigated and patched.
