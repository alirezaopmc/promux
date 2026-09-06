# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
