#!/usr/bin/env python3
"""Add or rotate role-free OttoRestart operator logins."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PBKDF2_ITERATIONS = 600_000
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
UPDATED_KEYS = (
    "OTTORESTART_AUTH_USERS_B64",
    "OTTORESTART_AUTH_USER",
    "OTTORESTART_AUTH_PASSWORD",
    "OTTORESTART_AUTH_SECRET",
    "OTTORESTART_OPERATOR_IDS",
)
KEY_LINE = re.compile(
    r"^(\s*)(OTTORESTART_AUTH_USERS_B64|OTTORESTART_AUTH_USER|"
    r"OTTORESTART_AUTH_PASSWORD|OTTORESTART_AUTH_SECRET|"
    r"OTTORESTART_OPERATOR_IDS)\s*=.*$"
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_b64url(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _hash_password(password: str) -> dict[str, str | int]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32
    )
    return {
        "scheme": "pbkdf2-sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": _b64url(salt),
        "digest": _b64url(digest),
    }


def _read_env_value(content: str, key: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*?)\s*$")
    matches = [match.group(1) for line in content.splitlines() if (match := pattern.match(line))]
    if len(matches) > 1:
        raise ValueError(f"{key} appears more than once in the env file")
    return matches[0].strip().strip("\"'") if matches else ""


def _load_users(content: str) -> dict[str, dict[str, str | int]]:
    encoded = _read_env_value(content, "OTTORESTART_AUTH_USERS_B64")
    if not encoded:
        return {}
    try:
        value = json.loads(_decode_b64url(encoded).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Existing OTTORESTART_AUTH_USERS_B64 is invalid; refusing to overwrite it") from error
    if not isinstance(value, dict):
        raise ValueError("Existing OttoRestart login map is not an object")

    users: dict[str, dict[str, str | int]] = {}
    casefolded: set[str] = set()
    for username, credential in value.items():
        if not isinstance(username, str) or not USERNAME.fullmatch(username) or username.casefold() in casefolded:
            raise ValueError("Existing OttoRestart login map contains an invalid or duplicate username")
        if not isinstance(credential, dict) or credential.get("scheme") != "pbkdf2-sha256":
            raise ValueError("Existing OttoRestart login map contains an unsupported credential")
        users[username] = credential
        casefolded.add(username.casefold())
    return users


def _atomic_update(path: Path, values: dict[str, str]) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing to replace a symlink: {path}")
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; create/configure it first")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Expected a regular file: {path}")
    if os.geteuid() != 0 and info.st_uid != os.geteuid():
        raise PermissionError("Run this script as the owner of the OttoRestart .env file")

    original = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines()
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        match = KEY_LINE.match(line)
        if match:
            key = match.group(2)
            updated.append(f"{match.group(1)}{key}={values[key]}")
            seen.add(key)
        else:
            updated.append(line)
    for key in UPDATED_KEYS:
        if key not in seen:
            updated.append(f"{key}={values[key]}")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        if os.geteuid() == 0:
            os.fchown(fd, info.st_uid, info.st_gid)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(newline.join(updated) + newline)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add role-free OttoRestart logins; each login has the same configured access."
    )
    parser.add_argument(
        "--username",
        action="append",
        dest="usernames",
        help="login to add or rotate; repeat for multiple logins (prompted if omitted)",
    )
    parser.add_argument(
        "--prompt-password",
        action="store_true",
        help="prompt securely for each password instead of generating random passwords",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env",
        help="OttoRestart env file (default: this project's .env)",
    )
    args = parser.parse_args()

    usernames = args.usernames or [input("Login [operator]: ").strip() or "operator"]
    if len(usernames) > 100 or any(not USERNAME.fullmatch(name) for name in usernames):
        parser.error("logins must be 3–64 ASCII letters, digits, dots, underscores, or hyphens; maximum 100")
    if len({name.casefold() for name in usernames}) != len(usernames):
        parser.error("the same login was supplied more than once")

    env_path = args.env_file.expanduser()
    if not env_path.is_absolute():
        env_path = Path.cwd() / env_path
    if env_path.is_symlink():
        print(f"Refusing to replace a symlink: {env_path}", file=sys.stderr)
        return 1
    if not env_path.exists():
        print(f"{env_path} does not exist; create/configure it first", file=sys.stderr)
        return 1

    try:
        content = env_path.read_text(encoding="utf-8")
        users = _load_users(content)
        known_names = {name.casefold() for name in users}
        supplied_names = {name.casefold() for name in usernames}
        if len(known_names | supplied_names) > 100:
            raise ValueError("OttoRestart supports at most 100 logins")

        generated_passwords: dict[str, str] = {}
        for username in usernames:
            if args.prompt_password:
                password = getpass.getpass(f"Password for {username}: ")
                confirm = getpass.getpass(f"Confirm password for {username}: ")
                if password != confirm:
                    raise ValueError(f"Passwords did not match for {username}")
                if len(password) < 8 or len(password) > 1024:
                    raise ValueError(f"Password for {username} must contain 8–1024 characters")
            else:
                password = secrets.token_urlsafe(32)
                generated_passwords[username] = password
            users[username] = _hash_password(password)

        lower_case_names: set[str] = set()
        for username in users:
            folded = username.casefold()
            if folded in lower_case_names:
                raise ValueError("OttoRestart login map contains duplicate case-insensitive usernames")
            lower_case_names.add(folded)

        encoded_users = _b64url(json.dumps(users, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        values = {
            "OTTORESTART_AUTH_USERS_B64": encoded_users,
            # Disable the previous shared login; the credential map takes precedence.
            "OTTORESTART_AUTH_USER": "",
            "OTTORESTART_AUTH_PASSWORD": "",
            "OTTORESTART_AUTH_SECRET": secrets.token_urlsafe(48),
            "OTTORESTART_OPERATOR_IDS": ",".join(sorted(users, key=str.casefold)),
        }
        _atomic_update(env_path, values)
    except (OSError, ValueError) as error:
        print(f"Could not update OttoRestart logins: {error}", file=sys.stderr)
        return 1

    print("OttoRestart logins updated. Password hashes are stored; no roles or user records were created.")
    for username in usernames:
        if username in generated_passwords:
            print(f"{username}: {generated_passwords[username]}")
        else:
            print(f"{username}: custom password saved (not echoed)")
    print(f"Credential map: {env_path} (permissions set to 0600)")
    print("All configured logins share the same JV/XL account access and permissions.")
    print("To activate the new logins in Docker, recreate only the OttoRestart API and frontend:")
    compose_env = shlex.quote(str(env_path))
    print(
        f"  cd {shlex.quote(str(PROJECT_ROOT))} && env"
        " -u OTTORESTART_AUTH_USERS_B64"
        " -u OTTORESTART_AUTH_USER"
        " -u OTTORESTART_AUTH_PASSWORD"
        " -u OTTORESTART_AUTH_SECRET"
        " -u OTTORESTART_OPERATOR_IDS"
        f" docker compose --env-file {compose_env}"
        " up -d --no-deps --no-build --force-recreate restart_backend restart_frontend"
    )
    print("This logs out active OttoRestart sessions; it does not touch OttoFlow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
