"""Secrets in Windows Credential Manager (via keyring), never in files or the repo.

Credential Manager caps one entry at ~2.5 KB and an Outlook token cache is bigger, so values are
split across numbered entries.
"""
import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "internship-autopilot"
CHUNK = 2000


def put(name: str, value: str) -> None:
    delete(name)
    parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)] or [""]
    keyring.set_password(SERVICE, f"{name}#n", str(len(parts)))
    for i, part in enumerate(parts):
        keyring.set_password(SERVICE, f"{name}#{i}", part)


def get(name: str) -> str | None:
    n = keyring.get_password(SERVICE, f"{name}#n")
    if n is None:
        return None
    return "".join(keyring.get_password(SERVICE, f"{name}#{i}") or "" for i in range(int(n)))


def delete(name: str) -> None:
    n = keyring.get_password(SERVICE, f"{name}#n")
    for key in [f"{name}#n", *(f"{name}#{i}" for i in range(int(n or 0)))]:
        try:
            keyring.delete_password(SERVICE, key)
        except PasswordDeleteError:
            pass
