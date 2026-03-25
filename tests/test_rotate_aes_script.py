import base64
from pathlib import Path
import json
import os
import subprocess
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine, text


def _build_codec_key(raw: str) -> bytes:
    key = raw.encode("utf-8")
    if len(key) < 32:
        key = key.ljust(32, b"0")
    return key[:32]


def _encrypt(raw_key: str, plaintext: str) -> str:
    key = _build_codec_key(raw_key)
    nonce = b"\x00" * 12
    cipher = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    payload = {"nonce": base64.b64encode(nonce).decode(), "cipher": base64.b64encode(cipher).decode()}
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _decrypt(raw_key: str, ciphertext: str) -> str:
    payload = json.loads(base64.b64decode(ciphertext).decode("utf-8"))
    nonce = base64.b64decode(payload["nonce"])
    cipher = base64.b64decode(payload["cipher"])
    plain = AESGCM(_build_codec_key(raw_key)).decrypt(nonce, cipher, None)
    return plain.decode("utf-8")


def _prepare_db(db_url: str, old_key: str) -> None:
    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, totp_secret_encrypted TEXT NULL)"))
        conn.execute(
            text(
                """
                CREATE TABLE exchange_accounts (
                    id INTEGER PRIMARY KEY,
                    api_key_encrypted TEXT NULL,
                    api_secret_encrypted TEXT NULL,
                    passphrase_encrypted TEXT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    resource_id TEXT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(text("INSERT INTO users (id, totp_secret_encrypted) VALUES (1, :totp)"), {"totp": _encrypt(old_key, "123456")})
        conn.execute(
            text(
                """
                INSERT INTO exchange_accounts (id, api_key_encrypted, api_secret_encrypted, passphrase_encrypted)
                VALUES (1, :api_key, :api_secret, :passphrase)
                """
            ),
            {
                "api_key": _encrypt(old_key, "api-key"),
                "api_secret": _encrypt(old_key, "api-secret"),
                "passphrase": _encrypt(old_key, "passphrase"),
            },
        )


def _run_rotate(*, db_url: str, old_key: str, new_key: str, mode: str, extra_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    script = Path("deploy/rotate_aes_master_key.py")
    command = [
        sys.executable,
        str(script),
        "--database-url",
        db_url,
        "--old-key",
        old_key,
        "--new-key",
        new_key,
        "--mode",
        mode,
        "--audit-user-id",
        "1",
    ]
    if extra_args:
        command.extend(extra_args)
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ},
    )


def test_rotate_aes_script_precheck_and_audit(tmp_path):
    old_key = "old-key-aaaaaaaaaaaaaaaaaaaaaaaa"
    new_key = "new-key-bbbbbbbbbbbbbbbbbbbbbbbb"
    db_file = tmp_path / "rotate_precheck.db"
    db_url = f"sqlite:///{db_file}"
    _prepare_db(db_url, old_key)

    result = _run_rotate(db_url=db_url, old_key=old_key, new_key=new_key, mode="precheck")
    payload = json.loads(result.stdout.strip())
    assert payload["mode"] == "precheck"
    assert payload["dry_run"] is True

    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        action = conn.execute(
            text("SELECT action FROM audit_events ORDER BY id DESC LIMIT 1")
        ).scalar_one()
        assert action == "aes_rotation_precheck"


def test_rotate_aes_script_execute_rotates_ciphertext(tmp_path):
    old_key = "old-key-cccccccccccccccccccccccc"
    new_key = "new-key-dddddddddddddddddddddddd"
    db_file = tmp_path / "rotate_execute.db"
    db_url = f"sqlite:///{db_file}"
    _prepare_db(db_url, old_key)

    _run_rotate(db_url=db_url, old_key=old_key, new_key=new_key, mode="execute")

    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        rotated = conn.execute(
            text("SELECT api_key_encrypted FROM exchange_accounts WHERE id = 1")
        ).scalar_one()
        action = conn.execute(
            text("SELECT action FROM audit_events ORDER BY id DESC LIMIT 1")
        ).scalar_one()
    assert _decrypt(new_key, rotated) == "api-key"
    assert action == "aes_rotation_execute"


def test_rotate_aes_script_rollback_restores_previous_key(tmp_path):
    old_key = "old-key-eeeeeeeeeeeeeeeeeeeeeeee"
    new_key = "new-key-ffffffffffffffffffffffff"
    db_file = tmp_path / "rotate_rollback.db"
    db_url = f"sqlite:///{db_file}"
    _prepare_db(db_url, old_key)

    _run_rotate(db_url=db_url, old_key=old_key, new_key=new_key, mode="execute")
    _run_rotate(db_url=db_url, old_key=new_key, new_key=old_key, mode="rollback")

    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        rotated_back = conn.execute(
            text("SELECT api_key_encrypted FROM exchange_accounts WHERE id = 1")
        ).scalar_one()
        action = conn.execute(
            text("SELECT action FROM audit_events ORDER BY id DESC LIMIT 1")
        ).scalar_one()
    assert _decrypt(old_key, rotated_back) == "api-key"
    assert action == "aes_rotation_rollback"


def test_rotate_aes_script_resume_from_checkpoint(tmp_path):
    old_key = "old-key-gggggggggggggggggggggggg"
    new_key = "new-key-hhhhhhhhhhhhhhhhhhhhhhhh"
    db_file = tmp_path / "rotate_resume.db"
    db_url = f"sqlite:///{db_file}"
    _prepare_db(db_url, old_key)

    result = _run_rotate(
        db_url=db_url,
        old_key=old_key,
        new_key=new_key,
        mode="execute",
        extra_args=["--resume-from", "exchange_accounts.api_secret_encrypted"],
    )
    payload = json.loads(result.stdout.strip())
    assert payload["resume_from"] == "exchange_accounts.api_secret_encrypted"

    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        api_key_cipher = conn.execute(
            text("SELECT api_key_encrypted FROM exchange_accounts WHERE id = 1")
        ).scalar_one()
        api_secret_cipher = conn.execute(
            text("SELECT api_secret_encrypted FROM exchange_accounts WHERE id = 1")
        ).scalar_one()
        action = conn.execute(
            text("SELECT action FROM audit_events ORDER BY id DESC LIMIT 1")
        ).scalar_one()

    # Resume starts from api_secret column, so api_key remains encrypted with old key.
    assert _decrypt(old_key, api_key_cipher) == "api-key"
    assert _decrypt(new_key, api_secret_cipher) == "api-secret"
    assert action == "aes_rotation_execute"
