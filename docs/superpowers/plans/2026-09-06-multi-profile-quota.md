# Multi-Profile Quota Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance `promux quota` to display a unified matrix table of all registered profiles when called without arguments, while preserving detailed single-account breakdowns when called with an account name.

**Architecture:** Refactor `cmd_quota` in `src/promux/cli.py` to extract a resilient `_fetch_account_quota` helper that isolates individual account token errors, and build a dual-mode formatter supporting all-profile matrix tables and single-profile detailed tables in both text and JSON modes.

**Tech Stack:** Python 3.10+ stdlib (`datetime`, `json`, `argparse`, `typing`), `pytest`.

## Global Constraints

- Zero external runtime dependencies (Python standard library only).
- Strict type safety and Ruff/Mypy compliance.
- Backward compatibility: `promux quota <name> --json` must continue returning a single account quota dictionary.

---

### Task 1: Extract Resilient `_fetch_account_quota` Helper

**Files:**
- Modify: `src/promux/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `StorageEngine`, `QuotaClient`, `_get_or_refresh_access_token`, `_refresh_token_file`.
- Produces: `_fetch_account_quota(storage: StorageEngine, target_name: str) -> tuple[QuotaSummary | None, str | None, str | None]` returning `(quota_summary, project_id, error_message)`.

- [ ] **Step 1: Write unit tests for `_fetch_account_quota`**

Add tests in `tests/test_cli.py`:
```python
def test_fetch_account_quota_success(tmp_path, sample_token_dict, monkeypatch):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.storage import StorageEngine
    from promux.cli import _fetch_account_quota, main
    from promux.quota import QuotaClient

    main(["save", "testacc", "--email", "test@test.com"])

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "proj-123"})
    monkeypatch.setattr(QuotaClient, "get_quota", lambda self, pid: QuotaSummary(
        gemini_5h_remaining=0.95,
        gemini_weekly_remaining=0.80,
        third_party_5h_remaining=1.0,
        third_party_weekly_remaining=0.40,
        gemini_5h_reset="2026-09-06T18:00:00Z",
    ))

    storage = StorageEngine()
    qs, pid, err = _fetch_account_quota(storage, "testacc")
    assert err is None
    assert pid == "proj-123"
    assert qs is not None
    assert qs.gemini_5h_remaining == 0.95


def test_fetch_account_quota_nonexistent(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from promux.storage import StorageEngine
    from promux.cli import _fetch_account_quota

    storage = StorageEngine()
    qs, pid, err = _fetch_account_quota(storage, "nonexistent")
    assert qs is None
    assert err is not None
    assert "not found" in err.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -k "test_fetch_account_quota" -v`  
Expected: FAIL with `ImportError: cannot import name '_fetch_account_quota' from 'promux.cli'`

- [ ] **Step 3: Implement `_fetch_account_quota` in `src/promux/cli.py`**

```python
def _fetch_account_quota(
    storage: StorageEngine,
    target_name: str,
) -> tuple[QuotaSummary | None, str | None, str | None]:
    """Fetch quota for an account, handling token resolution, refresh, and project ID discovery.

    Returns:
        tuple of (QuotaSummary or None, project_id or None, error_message or None)
    """
    acct = storage.get_account(target_name)
    if not acct:
        return None, None, f"Account '{target_name}' not found in vault."

    # Locate token file
    if target_name == storage.get_active_profile() and storage.live_token.exists():
        token_path = storage.live_token
    else:
        token_path = storage.accounts_dir / target_name / "antigravity-oauth-token"

    token_data = _read_token_data(token_path)
    access_token = _get_or_refresh_access_token(token_path)
    if not access_token:
        access_token = _extract_access_token(token_data)
    if not access_token:
        return None, None, f"Could not extract access token for account '{target_name}'."

    client = QuotaClient(token=access_token)
    project_id = acct.project_id
    if not project_id:
        try:
            meta = client.load_metadata()
            project_id = meta.get("project_id")
            if project_id:
                state = storage.load_state()
                if target_name in state.get("accounts", {}):
                    state["accounts"][target_name]["project_id"] = project_id
                    storage.save_state(state)
        except Exception as e:
            return None, None, f"Failed to load project metadata for '{target_name}': {e}"

    if not project_id:
        return None, None, f"Could not determine project ID for account '{target_name}'."

    try:
        qs = client.get_quota(project_id)
        return qs, project_id, None
    except Exception as e:
        if "401" in str(e):
            td = _read_token_data(token_path)
            refreshed = _refresh_token_file(token_path, td) if td else None
            if refreshed:
                client.token = refreshed
                try:
                    qs = client.get_quota(project_id)
                    return qs, project_id, None
                except Exception as retry_e:
                    return None, project_id, f"Error retrieving quota: {retry_e}"
            else:
                return None, project_id, f"Authentication failed (401): {e}"
        return None, project_id, f"Error retrieving quota: {e}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k "test_fetch_account_quota" -v`  
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "refactor(cli): extract resilient _fetch_account_quota helper function"
```

---

### Task 2: Implement Multi-Profile Matrix Table & Dual-Mode `cmd_quota`

**Files:**
- Modify: `src/promux/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `_fetch_account_quota`, `storage.list_accounts()`, `storage.get_active_profile()`.
- Produces: `cmd_quota(storage: StorageEngine, name: str | None, json_out: bool) -> int`.

- [ ] **Step 1: Write failing tests for multi-profile quota command**

Add tests to `tests/test_cli.py`:
```python
def test_cli_quota_all_profiles_matrix(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.quota import QuotaClient

    main(["save", "acc1", "--email", "1@test.com"])
    token2 = dict(sample_token_dict)
    token2["token"]["access_token"] = "acc2_token"
    (tmp_path / ".gemini" / "antigravity-oauth-token").write_text(json.dumps(token2))
    main(["save", "acc2", "--email", "2@test.com"])
    capsys.readouterr()

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "test-proj"})
    monkeypatch.setattr(QuotaClient, "get_quota", lambda self, pid: QuotaSummary(
        gemini_5h_remaining=0.961,
        gemini_weekly_remaining=0.82,
        third_party_5h_remaining=1.0,
        third_party_weekly_remaining=0.317,
        gemini_5h_reset="2026-09-06T16:58:03Z",
    ))

    rc = main(["quota"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "PROFILE" in out
    assert "GEMINI (5H)" in out
    assert "CLAUDE (5H)" in out
    assert "acc1" in out
    assert "acc2" in out
    assert "96.1%" in out


def test_cli_quota_all_profiles_json(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.quota import QuotaClient

    main(["save", "acc1"])
    capsys.readouterr()

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "test-proj"})
    monkeypatch.setattr(QuotaClient, "get_quota", lambda self, pid: QuotaSummary(
        gemini_5h_remaining=0.5,
        gemini_weekly_remaining=0.5,
        third_party_5h_remaining=0.5,
        third_party_weekly_remaining=0.5,
    ))

    rc = main(["quota", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["account"] == "acc1"
    assert data[0]["active"] is True
    assert "gemini" in data[0]


def test_cli_quota_single_profile_detail(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.quota import QuotaClient

    main(["save", "acc1"])
    capsys.readouterr()

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "test-proj"})
    monkeypatch.setattr(QuotaClient, "get_quota", lambda self, pid: QuotaSummary(
        gemini_5h_remaining=0.85,
        gemini_weekly_remaining=0.60,
        third_party_5h_remaining=0.20,
        third_party_weekly_remaining=0.40,
        gemini_5h_reset="2026-09-06T20:00:00Z",
    ))

    rc = main(["quota", "acc1"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "Quota for account 'acc1'" in out
    assert "MODEL GROUP" in out
    assert "Gemini Models" in out
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_cli.py -k "test_cli_quota_all_profiles_matrix" -v`  
Expected: FAIL (currently only checks single active profile)

- [ ] **Step 3: Implement dual-mode `cmd_quota` in `src/promux/cli.py`**

Update `cmd_quota`:
```python
def cmd_quota(storage: StorageEngine, name: str | None, json_out: bool) -> int:
    active_profile = storage.get_active_profile()

    # Single-profile detail mode
    if name is not None:
        qs, project_id, err = _fetch_account_quota(storage, name)
        if err or not qs:
            msg = err or f"Could not retrieve quota for '{name}'."
            if json_out:
                print(json.dumps({"error": msg}, indent=2))
            print(f"Error: {msg}", file=sys.stderr)
            return 1

        if json_out:
            res = {
                "account": name,
                "project_id": project_id,
                "gemini": {
                    "5h_remaining": qs.gemini_5h_remaining,
                    "5h_reset": qs.gemini_5h_reset,
                    "weekly_remaining": qs.gemini_weekly_remaining,
                    "weekly_reset": qs.gemini_weekly_reset,
                },
                "third_party": {
                    "5h_remaining": qs.third_party_5h_remaining,
                    "5h_reset": qs.third_party_5h_reset,
                    "weekly_remaining": qs.third_party_weekly_remaining,
                    "weekly_reset": qs.third_party_weekly_reset,
                },
            }
            print(json.dumps(res, indent=2))
            return 0

        active_tag = " [ACTIVE]" if name == active_profile else ""
        print(f"Quota for account '{name}' (project: {project_id}){active_tag}:\n")
        header = f"{'MODEL GROUP':<24} {'WINDOW':<10} {'REMAINING':<12} {'RESET TIME'}"
        print(header)
        print("-" * len(header))

        def _fmt(val: float | None) -> str:
            return f"{val * 100:.1f}%" if val is not None else "-"

        print(f"{'Gemini Models':<24} {'5h':<10} {_fmt(qs.gemini_5h_remaining):<12} {qs.gemini_5h_reset or '-'}")
        print(f"{'Gemini Models':<24} {'weekly':<10} {_fmt(qs.gemini_weekly_remaining):<12} {qs.gemini_weekly_reset or '-'}")
        print(f"{'Claude & GPT Models':<24} {'5h':<10} {_fmt(qs.third_party_5h_remaining):<12} {qs.third_party_5h_reset or '-'}")
        print(f"{'Claude & GPT Models':<24} {'weekly':<10} {_fmt(qs.third_party_weekly_remaining):<12} {qs.third_party_weekly_reset or '-'}")
        return 0

    # Multi-profile overview mode
    accounts = storage.list_accounts()
    if not accounts:
        msg = "No accounts registered in vault."
        if json_out:
            print(json.dumps([], indent=2))
        else:
            print(msg)
        return 0

    json_records = []
    text_rows = []

    for acct_meta in accounts:
        acct_name = acct_meta.name
        is_active = (acct_name == active_profile)
        qs, project_id, err = _fetch_account_quota(storage, acct_name)

        if json_out:
            record: dict[str, Any] = {
                "account": acct_name,
                "active": is_active,
                "project_id": project_id,
            }
            if qs:
                record["gemini"] = {
                    "5h_remaining": qs.gemini_5h_remaining,
                    "5h_reset": qs.gemini_5h_reset,
                    "weekly_remaining": qs.gemini_weekly_remaining,
                    "weekly_reset": qs.gemini_weekly_reset,
                }
                record["third_party"] = {
                    "5h_remaining": qs.third_party_5h_remaining,
                    "5h_reset": qs.third_party_5h_reset,
                    "weekly_remaining": qs.third_party_weekly_remaining,
                    "weekly_reset": qs.third_party_weekly_reset,
                }
            else:
                record["error"] = err
            json_records.append(record)
        else:
            active_mark = "*" if is_active else ""
            if qs:
                g5 = f"{qs.gemini_5h_remaining * 100:.1f}%" if qs.gemini_5h_remaining is not None else "-"
                gw = f"{qs.gemini_weekly_remaining * 100:.1f}%" if qs.gemini_weekly_remaining is not None else "-"
                c5 = f"{qs.third_party_5h_remaining * 100:.1f}%" if qs.third_party_5h_remaining is not None else "-"
                cw = f"{qs.third_party_weekly_remaining * 100:.1f}%" if qs.third_party_weekly_remaining is not None else "-"
                reset_raw = qs.gemini_5h_reset or qs.third_party_5h_reset or "-"
                reset_display = reset_raw.split("T")[-1].replace("Z", "") if "T" in reset_raw else reset_raw
            else:
                g5 = "[AUTH ERROR]" if "401" in str(err) else "[ERROR]"
                gw = "-"
                c5 = "-"
                cw = "-"
                reset_display = "-"
            text_rows.append((active_mark, acct_name, g5, gw, c5, cw, reset_display))

    if json_out:
        print(json.dumps(json_records, indent=2))
        return 0

    header = f"{'ACTIVE':<7} {'PROFILE':<17} {'GEMINI (5H)':<13} {'GEMINI (WK)':<13} {'CLAUDE (5H)':<13} {'CLAUDE (WK)':<13} {'NEXT RESET (UTC)'}"
    print(header)
    print("-" * len(header))
    for active_mark, acct_name, g5, gw, c5, cw, reset_display in text_rows:
        print(f"{active_mark:<7} {acct_name:<17} {g5:<13} {gw:<13} {c5:<13} {cw:<13} {reset_display}")

    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k "quota" -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(cli): display multi-profile quota matrix table and preserve single-account breakdown"
```

---

### Task 3: Documentation & Verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Updates user documentation and runs all static analyzers.

- [ ] **Step 1: Update README.md with the new multi-profile quota output example**

Update `README.md` §4 Quick Start and CLI Reference to showcase the multi-profile table:
```
ACTIVE  PROFILE           GEMINI (5H)   GEMINI (WK)   CLAUDE (5H)   CLAUDE (WK)   NEXT RESET (UTC)
---------------------------------------------------------------------------------------------------
*       main              96.1%         82.0%         100.0%        31.7%         16:58:03
        backup1           100.0%        95.0%         80.0%         60.0%         17:30:00
```
and note that `promux quota <name>` gives single-account detailed model group views.

- [ ] **Step 2: Run all linters, type checks, and full test suite**

Run:
1. `pytest -v`
2. `python3 -m py_compile src/promux/*.py`
Expected: 100% clean pass.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: update README with multi-profile quota matrix examples"
```
