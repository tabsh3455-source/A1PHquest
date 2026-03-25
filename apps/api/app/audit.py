from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def log_audit_event(
    db: Session,
    *,
    user_id: int,
    action: str,
    resource: str,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    record = AuditEvent(
        user_id=user_id,
        action=action,
        resource=resource,
        resource_id=resource_id,
        details_json=json.dumps(details or {}, ensure_ascii=False),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record

