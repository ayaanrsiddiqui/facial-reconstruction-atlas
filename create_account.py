"""Create a local account from the command line, on the application host.

Self-service registration through the web API is off by default
(ENTDATABASE_OPEN_REGISTRATION, see app.py). This is how the handful of
accounts needed before SSO lands get made: by someone with shell access to the
host, against the same database the application reads.

The password is never taken as an argument — it is prompted for, so it does
not land in shell history or in the process list.

An administrator may edit the case record — reorder and relabel a case's
photographs. An ordinary account only reads. Nobody is an administrator unless
named as one here.

Usage:
    python create_account.py <username>             # prompts for the password twice
    python create_account.py <username> --admin     # ... as an administrator
    python create_account.py --promote <username>
    python create_account.py --demote <username>
    python create_account.py --list
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys

from fastapi import HTTPException

from app import (
    DB_PATH,
    database_writes_allowed,
    ensure_auth_schema,
    hash_password,
    validate_password,
    validate_username,
)


def _connect() -> sqlite3.Connection:
    if not database_writes_allowed():
        raise SystemExit(
            "This deployment is configured read-only, so no account can be "
            "written. Point ENTDATABASE_DB_PATH at the writable database, or "
            "set ENTDATABASE_ALLOW_DB_WRITES=1 if this host is meant to write."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_accounts() -> int:
    with _connect() as conn:
        ensure_auth_schema(conn)
        rows = conn.execute(
            "SELECT username, is_admin, created_at FROM users ORDER BY created_at, username"
        ).fetchall()
    print(f"Database: {DB_PATH}")
    if not rows:
        print("No accounts yet.")
        return 0
    print(f"{len(rows)} account(s):")
    for row in rows:
        role = "administrator" if row["is_admin"] else "reads only"
        print(f"  {row['username']:24} {role:14} (created {row['created_at']})")
    return 0


def set_admin(username: str, *, is_admin: bool) -> int:
    with _connect() as conn:
        ensure_auth_schema(conn)
        changed = conn.execute(
            "UPDATE users SET is_admin = ? WHERE username = ?",
            (1 if is_admin else 0, username),
        ).rowcount
        conn.commit()
    if not changed:
        print(f"Refused: no account named {username!r}.", file=sys.stderr)
        return 2
    print(f"{username!r} is now {'an administrator' if is_admin else 'a reader'}.")
    return 0


def create_account(username: str, *, is_admin: bool = False) -> int:
    try:
        username = validate_username(username)
    except HTTPException as exc:
        print(f"Refused: {exc.detail}", file=sys.stderr)
        return 2

    password = getpass.getpass(f"Password for {username}: ")
    if password != getpass.getpass("Confirm password: "):
        print("Refused: the two passwords did not match.", file=sys.stderr)
        return 2
    try:
        validate_password(password)
    except HTTPException as exc:
        print(f"Refused: {exc.detail}", file=sys.stderr)
        return 2

    with _connect() as conn:
        ensure_auth_schema(conn)
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing is not None:
            print(f"Refused: an account named {username!r} already exists.", file=sys.stderr)
            return 2
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username, hash_password(password), 1 if is_admin else 0),
        )
        conn.commit()

    role = "administrator" if is_admin else "reads only"
    print(f"Created {username!r} ({role}) in {DB_PATH}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("username", nargs="?", help="account to create")
    group.add_argument("--list", action="store_true", help="list accounts and exit")
    group.add_argument("--promote", metavar="USERNAME", help="grant administrator")
    group.add_argument("--demote", metavar="USERNAME", help="revoke administrator")
    parser.add_argument(
        "--admin", action="store_true", help="create the account as an administrator"
    )
    args = parser.parse_args()

    if args.list:
        return list_accounts()
    if args.promote:
        return set_admin(args.promote, is_admin=True)
    if args.demote:
        return set_admin(args.demote, is_admin=False)
    return create_account(args.username, is_admin=args.admin)


if __name__ == "__main__":
    raise SystemExit(main())
