"""Checks for the USB installer bundle.

These tests are offline. They never install Python, never call pip and never
copy anything outside a temporary directory (the project rules require that
scripts are tested against temporary copies, not the real home directory).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install" / "install.ps1"
CMD = ROOT / "Install.cmd"
INSTRUCTIONS = ROOT / "INSTALL.txt"
BUILD = ROOT / "scripts" / "build_usb_bundle.ps1"
UPDATE = ROOT / "install" / "update.ps1"


def read(path: Path) -> str:
    assert path.is_file(), f"missing file: {path}"
    return path.read_text(encoding="utf-8-sig")


def test_bundle_entry_points_exist():
    for path in (INSTALL, CMD, INSTRUCTIONS, BUILD, UPDATE):
        assert path.is_file(), f"missing file: {path}"


def test_cmd_wrapper_runs_the_powershell_installer():
    text = read(CMD)
    assert text.isascii(), "the .cmd wrapper must stay ASCII-only"
    assert "install\\install.ps1" in text
    assert "ExecutionPolicy Bypass" in text
    assert "-NoProfile" in text
    assert "pause" in text


def test_cmd_wrapper_checks_the_installer_before_running_it():
    text = read(CMD)
    check_at = text.index('if not exist "%SCRIPT%"')
    run_at = text.index("powershell -NoProfile")
    assert check_at < run_at


def test_installer_does_not_require_administrator_rights():
    text = read(INSTALL).casefold()
    assert "runas" not in text
    assert "installallusers=0" in text
    assert "requires -runasadministrator" not in text


def test_installer_preflights_before_changing_anything():
    text = read(INSTALL)
    preflight = text.index("Checking that this PC can run the assistant")
    first_write = text.index("New-Item -ItemType Directory -Force -Path $InstallDir")
    assert preflight < first_write


def test_installer_uses_the_bundled_app_folder_as_source():
    text = read(INSTALL)
    assert "Join-Path $Script:Root 'app'" in text
    # No repository URL may be needed: the target PC has no GitHub access.
    assert "github.com" not in text.casefold()
    assert "git clone" not in text.casefold()


def test_installer_handles_mark_of_the_web():
    text = read(INSTALL)
    assert text.count("Unblock-File") >= 2


def test_installer_creates_a_ui_launcher_and_key_helper():
    text = read(INSTALL)
    assert "-m practice_assistant --ui" in text
    assert "setx OPENAI_API_KEY" in text
    assert "Start Practice Assistant.cmd" in text
    assert "Set API key.cmd" in text


def test_installer_is_idempotent_and_verifies_the_result():
    text = read(INSTALL)
    assert "already exists; it will be updated" in text
    assert "-m practice_assistant --help" in text


def test_launcher_and_verification_run_inside_the_app_folder():
    r"""Regression: the code lives in app\, not in the install root."""
    text = read(INSTALL)
    assert 'cd /d `"$InstallDir\\app`"' in text
    assert "Push-Location $target" in text


def test_installer_never_echoes_the_key():
    text = read(INSTALL)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "Write-" not in line or "$plain" not in line, line
        assert "echo %KEY%" not in line


def test_no_secret_or_author_path_leaks_into_the_scripts():
    secret = re.compile(r"sk-[A-Za-z0-9]{20,}")
    for path in (INSTALL, CMD, INSTRUCTIONS, BUILD):
        text = read(path)
        assert not secret.search(text), f"possible key in {path}"
        assert "E:\\StudioCode" not in text, f"author path in {path}"
        assert "/home/" not in text, f"author path in {path}"


def test_api_key_appears_only_as_an_environment_variable():
    for path in (INSTALL, INSTRUCTIONS):
        text = read(path)
        assert "OPENAI_API_KEY" in text
        assert "sk-" not in text


def test_instructions_cover_the_recovery_paths():
    text = read(INSTRUCTIONS).casefold()
    for topic in (
        "windows protected your pc",
        "install.log",
        "echo-guard",
        "unblock",
        "localappdata",
        "re-running",
    ):
        assert topic in text, f"instructions do not mention {topic}"


def test_build_script_reads_the_version_and_blocks_secrets():
    text = read(BUILD)
    assert "pyproject.toml" in text
    assert "version" in text
    assert "sk-[A-Za-z0-9]{20,}" in text
    assert "__pycache__" in text


def test_build_script_copies_the_installer_and_the_app():
    text = read(BUILD)
    for item in ("Install.cmd", "INSTALL.txt", "install", "practice_assistant"):
        assert item in text, f"build script does not copy {item}"


def test_installer_reports_a_readable_failure():
    text = read(INSTALL)
    assert "Installation stopped." in text
    assert "practice-assistant-install.log" in text


def test_preflight_checks_version_and_space():
    text = read(INSTALL)
    assert "MinPython" in text
    assert "700MB" in text or "700 MB" in text
    assert "Is64BitOperatingSystem" in text


def test_update_script_is_wired_into_the_launcher():
    text = read(INSTALL)
    assert "update.ps1" in text
    assert 'if not "%PA_NO_UPDATE%"=="1"' in text
    assert "Update Practice Assistant.cmd" in text


def test_update_can_be_disabled_without_touching_files():
    installer = read(INSTALL)
    updater = read(UPDATE)
    assert "PA_NO_UPDATE" in installer
    assert "PA_NO_UPDATE" in updater


def test_update_never_blocks_starting_the_program():
    text = read(UPDATE)
    # Every failure path must end in exit 0 so the assistant still starts.
    assert text.count("exit 0") >= 8
    assert "exit 1" not in text


def test_update_only_fast_forwards_and_respects_local_changes():
    text = read(UPDATE)
    assert "pull --ff-only" in text
    assert "status --porcelain" in text
    assert "--force" not in text.replace("$Force", "")


def test_update_script_has_a_quiet_mode_for_startup():
    text = read(UPDATE)
    assert "[switch]$Quiet" in text


def test_update_script_warns_instead_of_throwing():
    text = read(UPDATE)
    assert "Update skipped:" in text
    assert "catch" in text


def test_repository_url_is_configurable_and_optional():
    installer = read(INSTALL)
    assert "RepoUrl" in installer
    assert "update.ini" in installer
    assert "Press Enter to skip" in installer


def test_no_github_password_or_token_is_stored_by_the_installer():
    for path in (INSTALL, UPDATE, INSTRUCTIONS):
        text = read(path).casefold()
        assert "setx github" not in text
        assert "setx pa_github_token" not in text
        assert "password=" not in text
    # Credentials are left to Git's own encrypted store.
    assert "Git Credential Manager" in read(INSTRUCTIONS)
    assert "credential.helper=store" not in read(UPDATE)


def test_instructions_state_that_openrouter_alone_is_not_enough():
    text = read(INSTRUCTIONS).casefold()
    assert "openrouter" in text
    assert "not enough" in text


def test_installer_writes_the_update_configuration():
    text = read(INSTALL)
    assert "AutoUpdate=" in text
    assert "NoAutoUpdate" in text


def test_read_only_token_is_supported_for_private_repositories():
    text = read(UPDATE)
    assert "PA_GITHUB_TOKEN" in text
    assert "x-access-token" in text
    assert "http.extraheader" in text


def test_token_is_never_written_to_disk_by_the_updater():
    text = read(UPDATE)
    # The token must only ever be read from the environment and passed to git.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if (
            "PA_GITHUB_TOKEN" in line
            and "=" in line
            and line.strip().startswith("$env:PA_GITHUB_TOKEN")
        ):
            continue  # reading the variable is fine
        assert "Set-Content" not in line or "token" not in line.casefold(), line
        assert "Add-Content" not in line or "token" not in line.casefold(), line
        assert "Out-File" not in line or "token" not in line.casefold(), line


def test_updater_never_uses_the_plaintext_credential_store():
    text = read(UPDATE)
    assert "credential.helper=store" not in text
    # Git Credential Manager keeps credentials encrypted by Windows.
    assert "credential.helper=manager" in text


def test_updater_does_not_embed_credentials_in_the_remote_url():
    text = read(UPDATE)
    assert "@github.com" not in text
    assert "https://x-access-token" not in text


def test_token_is_not_logged_or_echoed():
    text = read(UPDATE)
    assert "Note $token" not in text
    assert "Write-Host $token" not in text


def test_installer_offers_a_read_only_token_and_clears_it_afterwards():
    text = read(INSTALL)
    assert "fine-grained token" in text
    assert '"Contents: read"' in text
    assert "Remove-Item Env:\\PA_GITHUB_TOKEN" in text


def test_installer_never_stores_the_github_token_with_setx():
    text = read(INSTALL)
    assert "setx PA_GITHUB_TOKEN" not in text
    assert "setx GITHUB" not in text


def test_instructions_describe_the_read_only_token():
    text = read(INSTRUCTIONS)
    lowered = text.casefold()
    assert "fine-grained" in lowered
    assert "contents" in lowered and "read-only" in lowered
    assert "PA_GITHUB_TOKEN" in text
    assert "never written to disk" in lowered


def test_openai_remains_the_only_provider():
    """The user decided to keep OpenAI only; no OpenRouter code may appear."""
    for path in (ROOT / "practice_assistant").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        assert "openrouter" not in text, f"OpenRouter reference in {path}"
    assert "openai" in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_installer_prefers_the_bundled_python_installer():
    text = read(INSTALL)
    assert "install\\python" in text
    assert "bundled installer" in text.lower()
    # A download is only the fallback.
    download_at = text.index("Downloading $url")
    bundled_at = text.index("install\\python")
    assert bundled_at < download_at


def test_installer_prefers_the_bundled_wheels():
    text = read(INSTALL)
    assert "install\\wheels" in text
    assert "--no-index" in text
    assert "--find-links" in text
    # pyproject must still be the fallback path.
    fallback_at = text.index("Downloading the packages from the internet")
    assert text.index("--no-index") < fallback_at


def test_pip_upgrade_cannot_break_an_offline_install():
    text = read(INSTALL)
    upgrade = text.index("--upgrade', 'pip'")
    surrounding = text[max(0, upgrade - 400) : upgrade]
    assert "try {" in surrounding
    assert "Could not refresh pip" in text


def test_offline_bundle_flag_bundles_python_and_wheels():
    text = read(BUILD)
    assert "[switch]$Offline" in text
    assert "install\\python" in text
    assert "install\\wheels" in text
    assert "python-3.14.3-amd64.exe" in text


def test_build_script_refuses_offline_without_a_cache():
    text = read(BUILD)
    assert "Bundled Python installer not found" in text
    assert "Wheel cache not found" in text
