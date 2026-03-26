from __future__ import annotations

import asyncio
from collections import defaultdict
import contextlib
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import re
from statistics import mean
from typing import Any

import httpx
from pydantic import ValidationError

from ..ai_provider_security import normalize_and_validate_provider_base_url
from ..db import SessionLocal
from ..events import build_ws_event
from ..kms import build_kms_provider
from ..models import (
    AccountBalanceSnapshot,
    AuditEvent,
    AiAutopilotDecisionRun,
    AiAutopilotPolicy,
    AiProviderCredential,
    ExchangeAccount,
    PositionSnapshot,
    Strategy,
)
from ..schemas import ComboGridDcaStrategyConfig, DcaStrategyConfig, GridStrategyConfig
from ..services.market_data import MarketDataService, normalize_market_symbol
from ..services.strategy_runtime_control import StrategyRuntimeControlError, StrategyRuntimeControlService
from ..tenant import with_tenant
from ..ws_manager import WsManager

logger = logging.getLogger(__name__)

_DEFAULT_POLICY_PROMPT = """
You are an AI strategy controller for a quantitative trading console.
You do not place exchange orders directly. You must only choose among the provided candidate strategies.
Return strict JSON with keys:
- action: one of hold, activate_strategy, stop_strategy, create_strategy_version
- target_strategy_id: integer or null
- confidence: number between 0 and 1
- rationale: short string
- parameter_overrides: object or null
Rules:
- Use only actions present in policy.allowed_actions. hold is always allowed.
- Use only candidate strategy ids provided in the context.
- create_strategy_version requires a target_strategy_id that points to the candidate strategy you want to clone.
- parameter_overrides may only contain safe strategy parameters:
  - grid: grid_count, grid_step_pct, base_order_size, max_grid_levels
  - dca: cycle_seconds, amount_per_cycle, price_offset_pct, min_order_volume
  - combo_grid_dca: grid_count, grid_step_pct, base_order_size, max_grid_levels, cycle_seconds, amount_per_cycle, price_offset_pct, min_order_volume
- Never change exchange_account_id or symbol.
- If market state is unclear or confidence is low, choose hold.
- Prefer minimal switching. Do not churn strategies without a clear advantage.
- If a strategy is already the best match and is active, choose hold unless stopping is clearly safer.
""".strip()

_AI_MUTATING_ACTIONS = {"activate_strategy", "stop_strategy", "create_strategy_version"}
_ALLOWED_OVERRIDE_FIELDS = {
    "grid": {"grid_count", "grid_step_pct", "base_order_size", "max_grid_levels"},
    "dca": {"cycle_seconds", "amount_per_cycle", "price_offset_pct", "min_order_volume"},
    "combo_grid_dca": {
        "grid_count",
        "grid_step_pct",
        "base_order_size",
        "max_grid_levels",
        "cycle_seconds",
        "amount_per_cycle",
        "price_offset_pct",
        "min_order_volume",
    },
}


class AiAutopilotError(RuntimeError):
    """Raised when AI autopilot cannot produce or apply a decision."""


class AiAutopilotService:
    def __init__(
        self,
        *,
        market_data_service: MarketDataService,
        ws_manager: WsManager,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._market_data_service = market_data_service
        self._ws_manager = ws_manager
        self._poll_interval_seconds = max(float(poll_interval_seconds), 1.0)
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._policy_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._kms = build_kms_provider()
        self._runtime_control = StrategyRuntimeControlService()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="ai-autopilot-loop")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def run_policy_once(
        self,
        *,
        policy_id: int,
        trigger_source: str = "manual",
        dry_run_override: bool | None = None,
    ) -> AiAutopilotDecisionRun:
        lock = self._policy_locks[int(policy_id)]
        async with lock:
            return await self._run_policy_once_locked(
                policy_id=int(policy_id),
                trigger_source=str(trigger_source or "manual"),
                dry_run_override=dry_run_override,
            )

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                due_policy_ids = self._list_due_policy_ids()
                for policy_id in due_policy_ids:
                    if self._stop_event.is_set():
                        break
                    try:
                        await self.run_policy_once(policy_id=policy_id, trigger_source="scheduler")
                    except Exception as exc:  # pragma: no cover - defensive scheduler guard
                        logger.warning("AI autopilot policy %s failed: %s", policy_id, exc)
            except Exception as exc:  # pragma: no cover - defensive scheduler guard
                logger.warning("AI autopilot loop error: %s", exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                continue

    def _list_due_policy_ids(self) -> list[int]:
        now = _utcnow()
        with SessionLocal() as db:
            policies = db.query(AiAutopilotPolicy).filter(AiAutopilotPolicy.status == "enabled").all()
            due_ids: list[int] = []
            for policy in policies:
                if policy.last_run_at is None:
                    due_ids.append(int(policy.id))
                    continue
                elapsed = (now - policy.last_run_at).total_seconds()
                if elapsed >= int(policy.decision_interval_seconds or 0):
                    due_ids.append(int(policy.id))
            return due_ids

    async def _run_policy_once_locked(
        self,
        *,
        policy_id: int,
        trigger_source: str,
        dry_run_override: bool | None,
    ) -> AiAutopilotDecisionRun:
        with SessionLocal() as db:
            policy = db.query(AiAutopilotPolicy).filter(AiAutopilotPolicy.id == policy_id).first()
            if not policy:
                raise AiAutopilotError(f"policy {policy_id} not found")
            policy_event = {
                "id": int(policy.id),
                "name": str(policy.name),
                "user_id": int(policy.user_id),
            }

            try:
                decision_run = await self._evaluate_policy(
                    db=db,
                    policy=policy,
                    trigger_source=trigger_source,
                    dry_run_override=dry_run_override,
                )
                policy.last_run_at = _utcnow()
                if decision_run.status != "error":
                    policy.last_decision_at = decision_run.created_at
                    policy.last_error = None
                db.add(policy)
                db.commit()
                db.refresh(decision_run)
            except Exception as exc:
                policy.last_run_at = _utcnow()
                policy.last_error = str(exc)[:500]
                db.add(policy)
                db.commit()
                error_run = self._build_error_run(
                    db=db,
                    policy=policy,
                    trigger_source=trigger_source,
                    error=str(exc),
                )
                db.commit()
                db.refresh(error_run)
                await self._emit_decision_event(policy=policy_event, decision_run=error_run)
                return error_run

        await self._emit_decision_event(policy=policy_event, decision_run=decision_run)
        return decision_run

    async def _evaluate_policy(
        self,
        *,
        db,
        policy: AiAutopilotPolicy,
        trigger_source: str,
        dry_run_override: bool | None,
    ) -> AiAutopilotDecisionRun:
        provider = (
            with_tenant(db.query(AiProviderCredential), AiProviderCredential, policy.user_id)
            .filter(AiProviderCredential.id == policy.provider_id)
            .first()
        )
        if not provider:
            raise AiAutopilotError(f"provider {policy.provider_id} not found")
        if not provider.is_active:
            raise AiAutopilotError(f"provider '{provider.name}' is disabled")

        exchange_account = (
            with_tenant(db.query(ExchangeAccount), ExchangeAccount, policy.user_id)
            .filter(ExchangeAccount.id == policy.exchange_account_id)
            .first()
        )
        if not exchange_account:
            raise AiAutopilotError(f"exchange_account_id {policy.exchange_account_id} not found")

        strategy_ids = _safe_load_json_list(policy.strategy_ids_json)
        candidates = (
            with_tenant(db.query(Strategy), Strategy, policy.user_id)
            .filter(Strategy.id.in_(strategy_ids))
            .all()
        )
        if len(candidates) != len(strategy_ids):
            raise AiAutopilotError("One or more candidate strategies were not found")

        normalized_symbol = normalize_market_symbol(exchange_account.exchange, policy.symbol)
        candidate_summaries = _build_candidate_summaries(
            candidates=candidates,
            expected_exchange_account_id=int(policy.exchange_account_id),
            expected_symbol=normalized_symbol,
            exchange=exchange_account.exchange,
        )
        current_active_strategy_id = next(
            (summary["id"] for summary in candidate_summaries if summary["is_running"]),
            None,
        )
        allowed_actions = _safe_load_action_list(policy.allowed_actions_json)

        candles = await self._market_data_service.fetch_history(
            exchange=exchange_account.exchange,
            symbol=normalized_symbol,
            interval=policy.interval,
            limit=200,
            is_testnet=exchange_account.is_testnet,
        )
        if len(candles) < 20:
            raise AiAutopilotError("not enough candle history for AI decision")

        factors = _compute_factor_snapshot(candles)
        context = _build_context_snapshot(
            db=db,
            policy=policy,
            exchange_account=exchange_account,
            normalized_symbol=normalized_symbol,
            candidate_summaries=candidate_summaries,
            current_active_strategy_id=current_active_strategy_id,
            candles=candles,
            factors=factors,
        )
        raw_response, decision = await self._call_provider(
            provider=provider,
            policy=policy,
            context=context,
        )

        action = str(decision.get("action") or "hold").strip().lower()
        target_strategy_id = _normalize_target_strategy_id(decision.get("target_strategy_id"))
        confidence = _normalize_confidence(decision.get("confidence"))
        rationale = str(decision.get("rationale") or "").strip()[:500]
        parameter_overrides = _normalize_parameter_overrides(decision.get("parameter_overrides"))
        execution_result, run_status, persisted_target_strategy_id = self._apply_decision(
            db=db,
            policy=policy,
            candidates=candidates,
            current_active_strategy_id=current_active_strategy_id,
            action=action,
            target_strategy_id=target_strategy_id,
            confidence=confidence,
            rationale=rationale,
            allowed_actions=allowed_actions,
            parameter_overrides=parameter_overrides,
            trigger_source=trigger_source,
            dry_run_override=dry_run_override,
        )

        run = AiAutopilotDecisionRun(
            user_id=policy.user_id,
            policy_id=policy.id,
            provider_id=provider.id,
            exchange_account_id=policy.exchange_account_id,
            trigger_source=trigger_source,
            status=run_status,
            action=action,
            target_strategy_id=persisted_target_strategy_id,
            confidence=confidence,
            rationale=rationale,
            factors_json=json.dumps(factors, ensure_ascii=False),
            context_json=json.dumps(context, ensure_ascii=False),
            raw_response_json=json.dumps(raw_response, ensure_ascii=False),
            execution_result_json=json.dumps(execution_result, ensure_ascii=False),
        )
        db.add(run)
        return run

    async def _call_provider(
        self,
        *,
        provider: AiProviderCredential,
        policy: AiAutopilotPolicy,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        api_key = self._kms.decrypt(provider.api_key_encrypted)
        base_url = normalize_and_validate_provider_base_url(provider.base_url)
        payload = {
            "model": provider.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "\n\n".join(
                        item
                        for item in [_DEFAULT_POLICY_PROMPT, str(policy.custom_prompt or "").strip()]
                        if item
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ],
            "temperature": 0.1,
        }

        endpoint = str(base_url or "").rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        response.raise_for_status()
        raw_payload = response.json()
        content = _extract_message_content(raw_payload)
        parsed = _extract_json_payload(content)
        return raw_payload, parsed

    def _apply_decision(
        self,
        *,
        db,
        policy: AiAutopilotPolicy,
        candidates: list[Strategy],
        current_active_strategy_id: int | None,
        action: str,
        target_strategy_id: int | None,
        confidence: float,
        rationale: str,
        allowed_actions: list[str],
        parameter_overrides: dict[str, Any],
        trigger_source: str,
        dry_run_override: bool | None,
    ) -> tuple[dict[str, Any], str, int | None]:
        allowed_strategy_ids = {int(strategy.id) for strategy in candidates}
        is_dry_run = bool(dry_run_override) if dry_run_override is not None else policy.execution_mode != "auto"
        candidates_by_id = {int(item.id): item for item in candidates}
        target_strategy = candidates_by_id.get(int(target_strategy_id or 0)) if target_strategy_id else None
        allowed_action_set = {str(item) for item in allowed_actions if str(item).strip()}

        if action not in {"hold", "activate_strategy", "stop_strategy", "create_strategy_version"}:
            return {"blocked_reason": f"unsupported action '{action}'"}, "blocked", None
        if action != "hold" and action not in allowed_action_set:
            return {
                "blocked_reason": f"action '{action}' is disabled by policy configuration",
                "allowed_actions": sorted(allowed_action_set),
            }, "blocked", target_strategy_id
        if action in {"activate_strategy", "create_strategy_version"} and target_strategy_id not in allowed_strategy_ids:
            return {"blocked_reason": "target_strategy_id is not in the candidate list"}, "blocked", None
        if confidence < float(policy.minimum_confidence or 0):
            return {
                "blocked_reason": "decision confidence below minimum threshold",
                "minimum_confidence": float(policy.minimum_confidence or 0),
            }, "blocked", target_strategy_id
        if action in _AI_MUTATING_ACTIONS and self._exceeded_action_limit(db=db, policy=policy):
            return {
                "blocked_reason": "policy max_actions_per_hour exceeded",
                "max_actions_per_hour": int(policy.max_actions_per_hour or 0),
            }, "blocked", target_strategy_id

        preview: dict[str, Any] | None = None
        if action == "create_strategy_version":
            if target_strategy is None:
                return {"blocked_reason": "target_strategy_id is required for create_strategy_version"}, "blocked", None
            try:
                preview = _prepare_generated_strategy_preview(
                    policy=policy,
                    base_strategy=target_strategy,
                    parameter_overrides=parameter_overrides,
                )
            except AiAutopilotError as exc:
                return {"blocked_reason": str(exc)}, "blocked", int(target_strategy.id)

        if is_dry_run:
            return {
                "mode": "dry_run",
                "message": "decision recorded without execution",
                "current_active_strategy_id": current_active_strategy_id,
                "requested_action": action,
                "target_strategy_id": target_strategy_id,
                "rationale": rationale,
                "parameter_overrides": parameter_overrides,
                "proposed_strategy": preview,
            }, "dry_run", target_strategy_id

        if action == "hold":
            return {
                "mode": "auto",
                "message": "AI chose to hold current strategy state",
                "current_active_strategy_id": current_active_strategy_id,
            }, "completed", current_active_strategy_id

        running_candidates = self._runtime_control.list_running_candidates(
            db=db,
            user_id=policy.user_id,
            strategy_ids=list(allowed_strategy_ids),
        )

        if action == "stop_strategy":
            selected = candidates_by_id.get(int(target_strategy_id or 0)) if target_strategy_id else None
            target = selected or (running_candidates[0] if running_candidates else None)
            if not target:
                return {"mode": "auto", "message": "no running candidate strategy to stop"}, "completed", None
            state = self._runtime_control.stop_strategy(
                db=db,
                user_id=policy.user_id,
                strategy=target,
                reason=f"ai_autopilot:{policy.id}:{trigger_source}",
            )
            return {
                "mode": "auto",
                "message": "strategy stopped by AI policy",
                "stopped_strategy_id": target.id,
                "runtime_ref": state.runtime_ref if state else target.runtime_ref,
            }, "executed", int(target.id)

        if action == "create_strategy_version":
            if target_strategy is None or preview is None:
                return {
                    "blocked_reason": "create_strategy_version requires a valid target strategy preview",
                }, "blocked", target_strategy_id
            generated_strategy = self._create_strategy_version(
                db=db,
                policy=policy,
                base_strategy=target_strategy,
                preview=preview,
                trigger_source=trigger_source,
                rationale=rationale,
            )
            stopped_ids: list[int] = []
            for running in running_candidates:
                if int(running.id) == int(generated_strategy.id):
                    continue
                self._runtime_control.stop_strategy(
                    db=db,
                    user_id=policy.user_id,
                    strategy=running,
                    reason=f"ai_autopilot_version_switch:{policy.id}:{trigger_source}",
                )
                stopped_ids.append(int(running.id))

            state = self._runtime_control.start_strategy(
                db=db,
                user_id=policy.user_id,
                strategy=generated_strategy,
                reason=f"ai_autopilot_version_switch:{policy.id}:{trigger_source}",
            )
            return {
                "mode": "auto",
                "message": "AI generated and activated a new strategy version",
                "template_strategy_id": int(target_strategy.id),
                "generated_strategy_id": int(generated_strategy.id),
                "generated_strategy_name": generated_strategy.name,
                "activated_strategy_id": int(generated_strategy.id),
                "stopped_strategy_ids": stopped_ids,
                "changed_fields": preview["changed_fields"],
                "parameter_overrides": parameter_overrides,
                "config": preview["config"],
                "runtime_ref": state.runtime_ref,
            }, "executed", int(generated_strategy.id)

        target = candidates_by_id[int(target_strategy_id)]
        if current_active_strategy_id == target.id and len(running_candidates) <= 1:
            return {
                "mode": "auto",
                "message": "target strategy is already active",
                "active_strategy_id": target.id,
            }, "completed", int(target.id)

        stopped_ids: list[int] = []
        for running in running_candidates:
            if int(running.id) == int(target.id):
                continue
            self._runtime_control.stop_strategy(
                db=db,
                user_id=policy.user_id,
                strategy=running,
                reason=f"ai_autopilot_switch:{policy.id}:{trigger_source}",
            )
            stopped_ids.append(int(running.id))

        state = self._runtime_control.start_strategy(
            db=db,
            user_id=policy.user_id,
            strategy=target,
            reason=f"ai_autopilot_switch:{policy.id}:{trigger_source}",
        )
        return {
            "mode": "auto",
            "message": "strategy activated by AI policy",
            "activated_strategy_id": target.id,
            "stopped_strategy_ids": stopped_ids,
            "runtime_ref": state.runtime_ref,
        }, "executed", int(target.id)

    def _create_strategy_version(
        self,
        *,
        db,
        policy: AiAutopilotPolicy,
        base_strategy: Strategy,
        preview: dict[str, Any],
        trigger_source: str,
        rationale: str,
    ) -> Strategy:
        generated = Strategy(
            user_id=policy.user_id,
            name=str(preview["name"]),
            template_key=base_strategy.template_key,
            strategy_type=base_strategy.strategy_type,
            config_json=json.dumps(preview["config"], ensure_ascii=False),
            status="stopped",
        )
        db.add(generated)
        db.flush()

        strategy_ids = _safe_load_json_list(policy.strategy_ids_json)
        if int(generated.id) not in strategy_ids:
            strategy_ids.append(int(generated.id))
            policy.strategy_ids_json = json.dumps(strategy_ids, ensure_ascii=False)
            db.add(policy)

        db.add(
            AuditEvent(
                user_id=policy.user_id,
                action="strategy_create",
                resource="strategy",
                resource_id=str(generated.id),
                details_json=json.dumps(
                    {
                        "template_key": generated.template_key,
                        "strategy_type": generated.strategy_type,
                        "trigger": "ai_autopilot",
                        "policy_id": int(policy.id),
                        "base_strategy_id": int(base_strategy.id),
                        "changed_fields": preview["changed_fields"],
                        "trigger_source": trigger_source,
                        "rationale": rationale,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.add(
            AuditEvent(
                user_id=policy.user_id,
                action="ai_policy_generated_strategy",
                resource="ai_policy",
                resource_id=str(policy.id),
                details_json=json.dumps(
                    {
                        "generated_strategy_id": int(generated.id),
                        "base_strategy_id": int(base_strategy.id),
                        "trigger_source": trigger_source,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()
        db.refresh(generated)
        db.refresh(policy)
        return generated

    def _exceeded_action_limit(self, *, db, policy: AiAutopilotPolicy) -> bool:
        window_start = _utcnow() - timedelta(hours=1)
        action_count = (
            db.query(AiAutopilotDecisionRun)
            .filter(
                AiAutopilotDecisionRun.policy_id == policy.id,
                AiAutopilotDecisionRun.created_at >= window_start,
                AiAutopilotDecisionRun.status == "executed",
            )
            .count()
        )
        return action_count >= int(policy.max_actions_per_hour or 0)

    def _build_error_run(
        self,
        *,
        db,
        policy: AiAutopilotPolicy,
        trigger_source: str,
        error: str,
    ) -> AiAutopilotDecisionRun:
        run = AiAutopilotDecisionRun(
            user_id=policy.user_id,
            policy_id=policy.id,
            provider_id=policy.provider_id,
            exchange_account_id=policy.exchange_account_id,
            trigger_source=trigger_source,
            status="error",
            action="hold",
            confidence=0,
            rationale=str(error)[:500],
            factors_json="{}",
            context_json="{}",
            raw_response_json="{}",
            execution_result_json=json.dumps({"error": str(error)[:500]}, ensure_ascii=False),
        )
        db.add(run)
        return run

    async def _emit_decision_event(
        self,
        *,
        policy: dict[str, Any],
        decision_run: AiAutopilotDecisionRun,
    ) -> None:
        payload = {
            "policy_id": int(policy["id"]),
            "policy_name": str(policy["name"]),
            "status": decision_run.status,
            "action": decision_run.action,
            "target_strategy_id": decision_run.target_strategy_id,
            "confidence": float(decision_run.confidence or 0),
            "created_at": decision_run.created_at.isoformat() if decision_run.created_at else None,
        }
        await self._ws_manager.push_to_user(
            int(policy["user_id"]),
            build_ws_event(
                event_type="ai_autopilot_decision",
                resource_id=f"ai-policy:{policy['id']}",
                dedupe_key=f"ai-policy:{policy['id']}:decision:{decision_run.id}",
                payload=payload,
            ),
        )


def _build_candidate_summaries(
    *,
    candidates: list[Strategy],
    expected_exchange_account_id: int,
    expected_symbol: str,
    exchange: str,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for strategy in candidates:
        config = _safe_load_json_dict(strategy.config_json)
        config_account_id = int(config.get("exchange_account_id") or 0)
        config_symbol = normalize_market_symbol(exchange, str(config.get("symbol") or ""))
        if config_account_id != expected_exchange_account_id or config_symbol != expected_symbol:
            raise AiAutopilotError(
                f"strategy {strategy.id} does not match the policy account/symbol scope"
            )
        summaries.append(
            {
                "id": int(strategy.id),
                "name": strategy.name,
                "template_key": str(strategy.template_key or ""),
                "strategy_type": strategy.strategy_type,
                "status": strategy.status,
                "is_running": strategy.status in {"starting", "running", "stopping"},
                "config": config,
            }
        )
    summaries.sort(key=lambda item: int(item["id"]))
    return summaries


def _build_context_snapshot(
    *,
    db,
    policy: AiAutopilotPolicy,
    exchange_account: ExchangeAccount,
    normalized_symbol: str,
    candidate_summaries: list[dict[str, Any]],
    current_active_strategy_id: int | None,
    candles: list[dict[str, Any]],
    factors: dict[str, Any],
) -> dict[str, Any]:
    balances = (
        with_tenant(db.query(AccountBalanceSnapshot), AccountBalanceSnapshot, policy.user_id)
        .filter(AccountBalanceSnapshot.exchange_account_id == policy.exchange_account_id)
        .all()
    )
    positions = (
        with_tenant(db.query(PositionSnapshot), PositionSnapshot, policy.user_id)
        .filter(
            PositionSnapshot.exchange_account_id == policy.exchange_account_id,
            PositionSnapshot.symbol == normalized_symbol,
        )
        .all()
    )
    balance_snapshot = [
        {
            "asset": item.asset,
            "free": float(item.free or 0),
            "locked": float(item.locked or 0),
            "total": float(item.total or 0),
        }
        for item in balances
    ][:10]
    position_snapshot = [
        {
            "side": item.side,
            "quantity": float(item.quantity or 0),
            "entry_price": float(item.entry_price or 0),
            "mark_price": float(item.mark_price or 0) if item.mark_price is not None else None,
            "unrealized_pnl": float(item.unrealized_pnl or 0) if item.unrealized_pnl is not None else None,
        }
        for item in positions
    ]

    return {
            "policy": {
                "id": int(policy.id),
                "name": policy.name,
                "symbol": normalized_symbol,
                "interval": policy.interval,
                "allowed_actions": _safe_load_action_list(policy.allowed_actions_json),
                "execution_mode": policy.execution_mode,
                "minimum_confidence": float(policy.minimum_confidence or 0),
                "max_actions_per_hour": int(policy.max_actions_per_hour or 0),
            },
        "exchange_account": {
            "id": int(exchange_account.id),
            "exchange": exchange_account.exchange,
            "is_testnet": bool(exchange_account.is_testnet),
            "account_alias": exchange_account.account_alias,
        },
        "factors": factors,
        "current_active_strategy_id": current_active_strategy_id,
        "candidates": candidate_summaries,
        "recent_candles": candles[-12:],
        "balances": balance_snapshot,
        "positions": position_snapshot,
        "generated_at": _utcnow().isoformat(),
    }


def _compute_factor_snapshot(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(item["close"]) for item in candles]
    highs = [float(item["high"]) for item in candles]
    lows = [float(item["low"]) for item in candles]
    volumes = [float(item.get("volume") or 0) for item in candles]

    close_now = closes[-1]
    close_5 = closes[-6] if len(closes) >= 6 else closes[0]
    close_15 = closes[-16] if len(closes) >= 16 else closes[0]
    returns = [_safe_pct_change(closes[index - 1], closes[index]) for index in range(1, len(closes))]
    atr_samples = [
        max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1]))
        for index in range(1, len(closes))
    ]

    ma_fast = mean(closes[-5:]) if len(closes) >= 5 else mean(closes)
    ma_slow = mean(closes[-20:]) if len(closes) >= 20 else mean(closes)
    avg_volume = mean(volumes[-20:]) if len(volumes) >= 20 else mean(volumes)
    latest_volume = volumes[-1] if volumes else 0.0

    return {
        "last_close": round(close_now, 8),
        "return_5_bars_pct": round(_safe_pct_change(close_5, close_now) * 100, 4),
        "return_15_bars_pct": round(_safe_pct_change(close_15, close_now) * 100, 4),
        "realized_volatility_pct": round(_stddev(returns[-20:]) * 100 if returns else 0.0, 4),
        "atr": round(mean(atr_samples[-14:]) if atr_samples else 0.0, 8),
        "ma_fast": round(ma_fast, 8),
        "ma_slow": round(ma_slow, 8),
        "ma_spread_pct": round(_safe_pct_change(ma_slow, ma_fast) * 100, 4),
        "rsi_14": round(_compute_rsi(closes, period=14), 4),
        "volume_surge_ratio": round((latest_volume / avg_volume) if avg_volume > 0 else 0.0, 4),
        "candle_count": len(candles),
    }


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AiAutopilotError("AI response does not contain choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AiAutopilotError("AI response does not contain a message object")
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise AiAutopilotError("AI response message is empty")
    return content.strip()


def _extract_json_payload(content: str) -> dict[str, Any]:
    cleaned = str(content or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, count=1).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise AiAutopilotError("AI response did not contain valid JSON")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise AiAutopilotError("AI response JSON must be an object")
    return payload


def _normalize_target_strategy_id(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))


def _normalize_parameter_overrides(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, raw in value.items():
        key_str = str(key or "").strip()
        if not key_str:
            continue
        normalized[key_str] = raw
    return normalized


def _safe_load_json_list(raw: str) -> list[int]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise AiAutopilotError(f"invalid strategy_ids_json: {exc}") from exc
    if not isinstance(value, list):
        raise AiAutopilotError("invalid strategy_ids_json: expected array")
    normalized: set[int] = set()
    for item in value:
        try:
            candidate = int(item)
        except (TypeError, ValueError):
            continue
        if candidate > 0:
            normalized.add(candidate)
    return sorted(normalized)


def _safe_load_json_dict(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise AiAutopilotError(f"invalid strategy config_json: {exc}") from exc
    if not isinstance(value, dict):
        raise AiAutopilotError("invalid strategy config_json: expected object")
    return value


def _safe_load_action_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise AiAutopilotError(f"invalid allowed_actions_json: {exc}") from exc
    if not isinstance(value, list):
        raise AiAutopilotError("invalid allowed_actions_json: expected array")
    order = {"activate_strategy": 0, "stop_strategy": 1, "create_strategy_version": 2}
    allowed = {key for key in order}
    normalized = {str(item) for item in value if str(item) in allowed}
    if not normalized:
        return ["activate_strategy", "stop_strategy", "create_strategy_version"]
    return sorted(normalized, key=lambda item: order[item])


def _prepare_generated_strategy_preview(
    *,
    policy: AiAutopilotPolicy,
    base_strategy: Strategy,
    parameter_overrides: dict[str, Any],
) -> dict[str, Any]:
    base_config = _safe_load_json_dict(base_strategy.config_json)
    strategy_type = str(base_strategy.strategy_type)
    allowed_fields = _ALLOWED_OVERRIDE_FIELDS.get(strategy_type)
    if not allowed_fields:
        raise AiAutopilotError(f"strategy_type '{strategy_type}' cannot be cloned by AI")
    if not parameter_overrides:
        raise AiAutopilotError("create_strategy_version requires non-empty parameter_overrides")

    unknown_fields = sorted(set(parameter_overrides.keys()) - allowed_fields)
    if unknown_fields:
        raise AiAutopilotError(
            "parameter_overrides contains unsupported fields: " + ", ".join(unknown_fields)
        )

    merged_config = dict(base_config)
    for field_name in allowed_fields:
        if field_name in parameter_overrides:
            merged_config[field_name] = parameter_overrides[field_name]

    try:
        if strategy_type == "grid":
            validated = GridStrategyConfig.model_validate(merged_config).model_dump()
        elif strategy_type == "dca":
            validated = DcaStrategyConfig.model_validate(merged_config).model_dump()
        else:
            validated = ComboGridDcaStrategyConfig.model_validate(merged_config).model_dump()
    except ValidationError as exc:
        first_error = exc.errors()[0] if exc.errors() else {}
        error_path = ".".join(str(item) for item in first_error.get("loc", []))
        error_message = str(first_error.get("msg") or "invalid parameter_overrides").strip()
        raise AiAutopilotError(
            f"invalid parameter_overrides for {strategy_type}: {error_path or 'config'} {error_message}"
        ) from exc

    if int(validated.get("exchange_account_id") or 0) != int(base_config.get("exchange_account_id") or 0):
        raise AiAutopilotError("AI may not change exchange_account_id")
    if str(validated.get("symbol") or "") != str(base_config.get("symbol") or ""):
        raise AiAutopilotError("AI may not change symbol")

    changed_fields = {
        key: validated[key]
        for key in sorted(allowed_fields)
        if validated.get(key) != base_config.get(key)
    }
    if not changed_fields:
        raise AiAutopilotError("parameter_overrides did not change any supported strategy parameters")

    return {
        "name": _build_generated_strategy_name(base_strategy.name, policy.id),
        "config": validated,
        "changed_fields": changed_fields,
        "base_strategy_id": int(base_strategy.id),
    }


def _build_generated_strategy_name(base_name: str, policy_id: int) -> str:
    timestamp = _utcnow().strftime("%Y%m%d-%H%M%S")
    label = f"{base_name} AI p{policy_id} {timestamp}".strip()
    return label[:128]


def _safe_pct_change(previous: float, current: float) -> float:
    if previous == 0:
        return 0.0
    return (float(current) - float(previous)) / float(previous)


def _stddev(values: list[float]) -> float:
    samples = [float(item) for item in values]
    if len(samples) < 2:
        return 0.0
    avg = mean(samples)
    variance = sum((sample - avg) ** 2 for sample in samples) / len(samples)
    return math.sqrt(max(variance, 0.0))


def _compute_rsi(closes: list[float], *, period: int) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for index in range(len(closes) - period, len(closes)):
        delta = float(closes[index]) - float(closes[index - 1])
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
