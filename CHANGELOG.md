# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-10-06

### Added
- Configurable per-account quota caching system (`QuotaCache`) stored in `~/.promux/cache/quota.json` (`0600` permissions).
- Configurable TTL support via `quota_cache_ttl_seconds` in `~/.promux/config.json` (defaults to 300s / 5 minutes).
- Environment variable override `PROMUX_QUOTA_CACHE_TTL` taking highest precedence for TTL configuration.
- `--no-cache` CLI flag for `promux quota` and `promux switch --smart` to bypass cached quotas and query the API directly.
- JSON output support with `"cached": true/false` field for quota responses.
- Automatic cache invalidation upon authentication failure (401 Unauthorized / revoked token).
- Shell auto-completion updates for bash and zsh supporting `--no-cache`.

## [0.3.1] - 2026-09-30

### Changed
- Significantly improved `promux quota` and `promux switch --smart` performance via parallel evaluation.

## [0.3.0] - 2026-09-11

### Added
- Smart quota-aware profile switching via `promux switch --smart [--model {gemini,claude,gpt}]`.
- Automated candidate evaluation querying live Cloud Code Assist API quotas across standby profiles.
- Model tier filtering supporting `gemini` (default) and third-party models (`claude`, `gpt`).
- Smart ranking metric prioritizing 5-hour available quota fraction, weekly quota, and LRU tie-breaker.
- Conditional active profile cooldown: quarantines departed account only if its quota is exhausted.

## [0.2.0] - 2026-09-07

### Added
- Two-tier resilient OAuth token renewal:
  - **Tier 1 (Native HTTP):** Direct POST to Google OAuth endpoint using embedded official Antigravity client credentials with zero external dependencies and automatic refresh token rotation.
  - **Tier 2 (Headless `agy` CLI):** Automated background fallback under POSIX file lock with safe live token backup and restoration.
- `promux refresh [name] [--force] [--json]` CLI command to inspect and refresh tokens across all accounts or single target with ASCII table and JSON formatting.
- Proactive background token renewal in `LogWatcher` (`promux watch`) to maintain token freshness before expiration.
- Auto-refresh and atomic synchronization (`0600` permissions) during `promux switch` and `promux quota`.
- Clean error messaging and actionable hints for revoked Google OAuth refresh tokens (`invalid_grant`).

## [0.1.0] - 2026-09-06

### Added
- Multi-profile Antigravity CLI token vault management (`promux save`, `list`, `switch`, `remove`, `whoami`).
- Real-time Cloud Code Assist quota inspection (`promux quota`) supporting Gemini and Claude/GPT quota windows.
- Least-Recently Used (LRU) failover rotation engine with configurable cooldown windows (`promux next`).
- Reactive log-tailing daemon (`promux watch`) tracking `cli.log` and session logs with automatic reset-time parsing and hot-swap failover.
- Automated OAuth token refreshing for expired tokens.
- Cross-platform POSIX file locking (`fcntl.flock`) and atomic file swaps (`.tmp` + `os.replace`).
- Comprehensive test suite with 80 unit and integration tests.
- Full GitHub community health files (CI workflows, contributing guide, code of conduct, security policy, and issue templates).
