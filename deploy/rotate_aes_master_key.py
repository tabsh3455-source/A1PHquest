#!/usr/bin/env python3
"""Rotate local AES master key for encrypted database fields."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine, text


@dataclass(slots=True)
class LocalAesCodec:
    key_material: bytes

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        cipher = AESGCM(self.key_material).encrypt(nonce, plaintext.encode("utf-8"), None)
        payload = {"nonce": base64.b64encode(nonce).decode(), "cipher": base64.b64encode(cipher).decode()}
        return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        payload = json.loads(base64.b64decode(ciphertext).decode("utf-8"))
        nonce = base64.b64decode(payload["nonce"])
        cipher = base64.b64decode(payload["cipher"])
        plain = AESGCM(self.key_material).decrypt(nonce, cipher, None)
        return plain.decode("utf-8")


def _build_codec(raw_key: str) -> LocalAesCodec:
    key = raw_key.encode("utf-8")
    if len(key) != 32:
        raise ValueError("AES key must be exactly 32 bytes")
    return LocalAesCodec(key_material=key)


def _rotate_table_column(conn, *, table: str, id_column: str, cipher_column: str, old: LocalAesCodec, new: LocalAesCodec, dry_run: bool) -> int:
    rows = conn.execute(
        text(f"SELECT {id_column}, {cipher_column} FROM {table} WHERE {cipher_column} IS NOT NULL")
    ).mappings().all()
    changed = 0
    for row in rows:
        source = row[cipher_column]
        if not source:
            continue
        plaintext = old.decrypt(str(source))
        rotated = new.encrypt(plaintext)
        changed += 1
        if not dry_run:
            conn.execute(
                text(f"UPDATE {table} SET {cipher_column} = :value WHERE {id_column} = :id_value"),
                {"value": rotated, "id_value": row[id_column]},
            )
    return changed


def _resolve_audit_user_id(conn, requested_id: int | None) -> int | None:
    if requested_id and requested_id > 0:
        exists = conn.execute(
            text("SELECT id FROM users WHERE id = :user_id"),
            {"user_id": requested_id},
        ).scalar()
        if exists:
            return int(exists)
    fallback = conn.execute(text("SELECT id FROM users ORDER BY id ASC LIMIT 1")).scalar()
    return int(fallback) if fallback else None


def _write_audit_event(conn, *, user_id: int | None, action: str, details: dict) -> None:
    if not user_id:
        return
    try:
        conn.execute(
            text(
                """
                INSERT INTO audit_events (user_id, action, resource, resource_id, details_json, created_at)
                VALUES (:user_id, :action, 'kms', NULL, :details_json, :created_at)
                """
            ),
            {
                "user_id": user_id,
                "action": action,
                "details_json": json.dumps(details, ensure_ascii=False),
                "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
        )
    except Exception:
        # Rotation should still proceed when audit table/schema is unavailable.
        pass


def _target_key(table: str, cipher_column: str) -> str:
    return f"{table}.{cipher_column}"


def _select_targets(
    targets: list[tuple[str, str, str]],
    *,
    resume_from: str | None,
) -> list[tuple[str, str, str]]:
    """
    Allow operators to resume from a known checkpoint target after a failed run.
    """
    if not resume_from:
        return targets
    normalized = resume_from.strip().lower()
    for index, (table, _, cipher_column) in enumerate(targets):
        if _target_key(table, cipher_column).lower() == normalized:
            return targets[index:]
    raise ValueError(f"Unknown --resume-from target: {resume_from}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate local AES master key in-place.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./a1phquest_dev.db"),
    )
    parser.add_argument("--old-key", default=os.getenv("OLD_AES_MASTER_KEY", ""))
    parser.add_argument("--new-key", default=os.getenv("NEW_AES_MASTER_KEY", ""))
    parser.add_argument(
        "--mode",
        choices=["precheck", "execute", "rollback"],
        default="execute",
        help="precheck: decrypt/encrypt validation only; execute: rotate; rollback: rotate back",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--audit-user-id", type=int, default=int(os.getenv("ROTATION_AUDIT_USER_ID", "0")))
    parser.add_argument(
        "--resume-from",
        default=os.getenv("ROTATION_RESUME_FROM", ""),
        help="Optional checkpoint target to resume from, for example exchange_accounts.api_secret_encrypted",
    )
    args = parser.parse_args()

    if not args.old_key or not args.new_key:
        raise SystemExit("OLD_AES_MASTER_KEY and NEW_AES_MASTER_KEY are required")

    old_codec = _build_codec(args.old_key)
    new_codec = _build_codec(args.new_key)
    engine = create_engine(args.database_url, future=True)

    # We rotate only columns encrypted by local AES provider.
    targets = [
        ("exchange_accounts", "id", "api_key_encrypted"),
        ("exchange_accounts", "id", "api_secret_encrypted"),
        ("exchange_accounts", "id", "passphrase_encrypted"),
        ("users", "id", "totp_secret_encrypted"),
    ]

    dry_run = bool(args.dry_run or args.mode == "precheck")
    summary: dict[str, int] = {}
    action_name = f"aes_rotation_{args.mode}"
    resume_from = args.resume_from.strip() or None
    with engine.begin() as conn:
        audit_user_id = _resolve_audit_user_id(conn, args.audit_user_id)
        _write_audit_event(
            conn,
            user_id=audit_user_id,
            action="aes_rotation_started",
            details={
                "mode": args.mode,
                "dry_run": dry_run,
                "resume_from": resume_from,
                "target_count": len(targets),
            },
        )
        selected_targets: list[tuple[str, str, str]] = []
        current_target = ""
        try:
            selected_targets = _select_targets(targets, resume_from=resume_from)
            for index, (table, id_col, cipher_col) in enumerate(selected_targets, start=1):
                current_target = _target_key(table, cipher_col)
                count = _rotate_table_column(
                    conn,
                    table=table,
                    id_column=id_col,
                    cipher_column=cipher_col,
                    old=old_codec,
                    new=new_codec,
                    dry_run=dry_run,
                )
                summary[current_target] = count
                # Persist per-column checkpoint so operators can resume accurately.
                _write_audit_event(
                    conn,
                    user_id=audit_user_id,
                    action="aes_rotation_checkpoint",
                    details={
                        "mode": args.mode,
                        "dry_run": dry_run,
                        "target": current_target,
                        "index": index,
                        "total_targets": len(selected_targets),
                        "rotated_count": count,
                    },
                )
        except Exception as exc:
            _write_audit_event(
                conn,
                user_id=audit_user_id,
                action="aes_rotation_failed",
                details={
                    "mode": args.mode,
                    "dry_run": dry_run,
                    "error": str(exc),
                    "progress": summary,
                    "failed_target": current_target,
                    "resume_from": current_target or resume_from,
                },
            )
            raise

        _write_audit_event(
            conn,
            user_id=audit_user_id,
            action=action_name,
            details={
                "mode": args.mode,
                "dry_run": dry_run,
                "resume_from": resume_from,
                "completed_targets": list(summary.keys()),
                "rotated_columns": summary,
            },
        )

    print(
        json.dumps(
            {
                "mode": args.mode,
                "dry_run": dry_run,
                "database_url": args.database_url,
                "resume_from": resume_from,
                "rotated_columns": summary,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
