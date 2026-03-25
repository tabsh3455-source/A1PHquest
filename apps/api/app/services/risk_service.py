from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AccountBalanceSnapshot, AuditEvent, PositionSnapshot, RiskRule, TradeFillSnapshot
from ..tenant import with_tenant

settings = get_settings()


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: str
    code: str = "ok"
    realized_daily_loss: float = 0.0
    evaluated_daily_loss: float = 0.0
    evaluated_position_ratio: float | None = None


@dataclass(slots=True)
class _SymbolPosition:
    """
    Track open position state for realized PnL reconstruction.

    `qty` sign convention:
    - positive: net long
    - negative: net short
    """

    qty: float = 0.0
    avg_price: float = 0.0


class RiskService:
    def is_circuit_breaker_enabled(self, db: Session, *, user_id: int) -> bool:
        """
        Read user-level circuit breaker switch from risk rule.

        Returning False when rule is missing keeps behavior explicit: users opt
        in by creating a risk rule instead of getting implicit stop-all behavior.
        """
        rule = with_tenant(db.query(RiskRule), RiskRule, user_id).first()
        if not rule:
            return False
        return bool(rule.circuit_breaker_enabled)

    def evaluate_order(
        self,
        db: Session,
        *,
        user_id: int,
        order_notional: float,
        projected_daily_loss: float = 0.0,
        projected_position_ratio: float = 0.0,
        account_id: int | None = None,
        symbol: str | None = None,
        now: datetime | None = None,
    ) -> RiskDecision:
        rule = with_tenant(db.query(RiskRule), RiskRule, user_id).first()
        if not rule:
            return RiskDecision(True, "No risk rule configured")

        max_daily_loss = float(rule.max_daily_loss)
        max_order_notional = float(rule.max_order_notional)
        max_position_ratio = float(rule.max_position_ratio)
        # Allow callers (especially tests/e2e replay tools) to pin evaluation time.
        # Production callers can omit this and keep using current UTC wall clock.
        realized_daily_loss = self.calculate_daily_realized_loss(db, user_id=user_id, now=now)
        # Keep backward compatibility for dry-run callers that still send projected
        # losses, but the baseline value now always comes from server-side fills.
        evaluated_daily_loss = realized_daily_loss + max(float(projected_daily_loss), 0.0)
        evaluated_position_ratio: float | None = None

        if rule.circuit_breaker_enabled and max_daily_loss > 0 and evaluated_daily_loss > max_daily_loss:
            return RiskDecision(
                False,
                "Daily realized loss exceeds threshold",
                code="daily_loss_limit_exceeded",
                realized_daily_loss=realized_daily_loss,
                evaluated_daily_loss=evaluated_daily_loss,
            )
        if max_position_ratio > 0:
            if account_id is not None and symbol:
                evaluated_position_ratio = self._estimate_projected_position_ratio(
                    db,
                    user_id=user_id,
                    account_id=account_id,
                    symbol=symbol,
                    order_notional=order_notional,
                )
                if evaluated_position_ratio is None:
                    return RiskDecision(
                        False,
                        "Position ratio cannot be evaluated until account balance snapshots are synced",
                        code="position_ratio_context_unavailable",
                        realized_daily_loss=realized_daily_loss,
                        evaluated_daily_loss=evaluated_daily_loss,
                    )
            else:
                evaluated_position_ratio = max(float(projected_position_ratio), 0.0)

        if max_order_notional > 0 and order_notional > max_order_notional:
            return RiskDecision(
                False,
                "Order notional exceeds limit",
                code="order_notional_limit_exceeded",
                realized_daily_loss=realized_daily_loss,
                evaluated_daily_loss=evaluated_daily_loss,
                evaluated_position_ratio=evaluated_position_ratio,
            )
        if (
            max_position_ratio > 0
            and evaluated_position_ratio is not None
            and evaluated_position_ratio > max_position_ratio
        ):
            return RiskDecision(
                False,
                "Position ratio exceeds limit",
                code="position_ratio_limit_exceeded",
                realized_daily_loss=realized_daily_loss,
                evaluated_daily_loss=evaluated_daily_loss,
                evaluated_position_ratio=evaluated_position_ratio,
            )

        return RiskDecision(
            True,
            "Risk checks passed",
            code="ok",
            realized_daily_loss=realized_daily_loss,
            evaluated_daily_loss=evaluated_daily_loss,
            evaluated_position_ratio=evaluated_position_ratio,
        )

    def evaluate_cancel_rate(self, db: Session, *, user_id: int) -> RiskDecision:
        rule = with_tenant(db.query(RiskRule), RiskRule, user_id).first()
        if not rule:
            return RiskDecision(True, "No risk rule configured")

        max_cancels = int(rule.max_cancel_rate_per_minute)
        if max_cancels <= 0:
            return RiskDecision(True, "Cancel rate check disabled")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        window_start = now - timedelta(minutes=1)
        cancel_count = (
            with_tenant(db.query(AuditEvent), AuditEvent, user_id)
            .filter(AuditEvent.action == "order_cancel", AuditEvent.created_at >= window_start)
            .count()
        )
        if cancel_count >= max_cancels:
            return RiskDecision(False, "Cancel rate exceeds limit")
        return RiskDecision(True, "Cancel rate check passed")

    def evaluate_rejection_burst(self, db: Session, *, user_id: int) -> RiskDecision:
        """
        Trigger-level check for repeated risk rejections in a short window.

        This is a secondary defense to stop runtimes when user keeps hitting risk
        limits in bursts (e.g. runaway strategy loop).
        """
        threshold = max(int(settings.risk_rejection_burst_threshold), 1)
        window_seconds = max(int(settings.risk_rejection_burst_window_seconds), 1)
        cooldown_seconds = max(int(settings.risk_rejection_burst_cooldown_seconds), 1)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        window_start = now - timedelta(seconds=window_seconds)
        rejection_count = (
            with_tenant(db.query(AuditEvent), AuditEvent, user_id)
            .filter(
                AuditEvent.created_at >= window_start,
                AuditEvent.action.in_(("order_submit_rejected_risk", "order_cancel_rejected_risk")),
            )
            .count()
        )
        if rejection_count < threshold:
            return RiskDecision(True, "Risk rejection burst check passed")

        last_trigger_time = (
            with_tenant(db.query(AuditEvent), AuditEvent, user_id)
            .filter(
                AuditEvent.action == "circuit_breaker_trigger",
                AuditEvent.created_at >= now - timedelta(seconds=cooldown_seconds),
            )
            .order_by(AuditEvent.created_at.desc())
            .first()
        )
        if last_trigger_time:
            return RiskDecision(True, "Risk rejection burst already handled in cooldown window")

        return RiskDecision(
            False,
            f"Risk rejection burst detected ({rejection_count} in {window_seconds}s)",
        )

    def calculate_daily_realized_loss(
        self,
        db: Session,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> float:
        """
        Calculate today's realized loss from persisted fills.

        We rebuild a lightweight per-symbol position book from trade snapshots and
        realize PnL only on closed quantities. Open quantities are intentionally
        excluded so this metric reflects realized (not mark-to-market) loss.
        """
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
        day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        rows = (
            with_tenant(db.query(TradeFillSnapshot), TradeFillSnapshot, user_id)
            .filter(
                TradeFillSnapshot.trade_time >= day_start,
                TradeFillSnapshot.trade_time < day_end,
            )
            .order_by(TradeFillSnapshot.trade_time.asc(), TradeFillSnapshot.id.asc())
            .all()
        )

        positions: dict[str, _SymbolPosition] = {}
        realized_pnl_quote = 0.0
        for fill in rows:
            symbol = str(fill.symbol or "").upper().strip()
            if not symbol:
                continue
            side = str(fill.side or "").upper().strip()
            if side not in {"BUY", "SELL"}:
                continue
            quantity = float(fill.quantity or 0.0)
            price = float(fill.price or 0.0)
            if quantity <= 0 or price <= 0:
                continue
            signed_qty = quantity if side == "BUY" else -quantity

            position = positions.get(symbol, _SymbolPosition())
            if _same_direction(position.qty, signed_qty) or _is_zero(position.qty):
                position = _increase_position(position, signed_qty=signed_qty, price=price)
            else:
                close_qty = min(abs(position.qty), abs(signed_qty))
                if position.qty > 0:
                    # Closing long with sell.
                    realized_pnl_quote += (price - position.avg_price) * close_qty
                else:
                    # Closing short with buy.
                    realized_pnl_quote += (position.avg_price - price) * close_qty
                position = _apply_close_and_reverse(position, signed_qty=signed_qty, price=price, close_qty=close_qty)

            realized_pnl_quote -= _estimate_fee_in_quote(fill)
            positions[symbol] = position

        if realized_pnl_quote >= 0:
            return 0.0
        return abs(realized_pnl_quote)

    def _estimate_projected_position_ratio(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
        symbol: str,
        order_notional: float,
    ) -> float | None:
        """
        Estimate post-order position ratio from server-side snapshots only.

        The estimate is intentionally conservative:
        - exposure = absolute notional of all persisted positions on the account
        - collateral = quote-asset balance when available, otherwise stable-asset collateral

        Returning None means live risk should fail closed until the account has
        been synced and server snapshots exist.
        """
        collateral_notional = _estimate_collateral_notional(
            db,
            user_id=user_id,
            account_id=account_id,
            symbol=symbol,
        )
        if collateral_notional <= 0:
            return None

        existing_exposure = _estimate_position_exposure_notional(
            db,
            user_id=user_id,
            account_id=account_id,
        )
        projected_exposure = existing_exposure + max(float(order_notional), 0.0)
        return projected_exposure / collateral_notional


def _same_direction(current_qty: float, signed_qty: float) -> bool:
    return (current_qty > 0 and signed_qty > 0) or (current_qty < 0 and signed_qty < 0)


def _is_zero(value: float, *, eps: float = 1e-12) -> bool:
    return abs(value) <= eps


def _increase_position(position: _SymbolPosition, *, signed_qty: float, price: float) -> _SymbolPosition:
    new_qty = position.qty + signed_qty
    if _is_zero(new_qty):
        return _SymbolPosition()
    total_notional = abs(position.qty) * position.avg_price + abs(signed_qty) * price
    avg_price = total_notional / abs(new_qty)
    return _SymbolPosition(qty=new_qty, avg_price=avg_price)


def _apply_close_and_reverse(
    position: _SymbolPosition,
    *,
    signed_qty: float,
    price: float,
    close_qty: float,
) -> _SymbolPosition:
    remaining_position_qty = abs(position.qty) - close_qty
    incoming_remainder_qty = abs(signed_qty) - close_qty

    if incoming_remainder_qty > 0:
        # Reversal: the extra quantity opens a new position at current trade price.
        direction = 1.0 if signed_qty > 0 else -1.0
        return _SymbolPosition(qty=direction * incoming_remainder_qty, avg_price=price)
    if remaining_position_qty > 0:
        direction = 1.0 if position.qty > 0 else -1.0
        return _SymbolPosition(qty=direction * remaining_position_qty, avg_price=position.avg_price)
    return _SymbolPosition()


def _estimate_fee_in_quote(fill: TradeFillSnapshot) -> float:
    fee = abs(float(fill.fee or 0.0))
    if fee <= 0:
        return 0.0

    fee_asset = str(fill.fee_asset or "").upper()
    symbol = str(fill.symbol or "").upper()
    base_asset, quote_asset = _split_symbol_assets(symbol)

    # Unknown fee asset is treated as quote-equivalent fallback so risk stays conservative.
    if not fee_asset or (quote_asset and fee_asset == quote_asset):
        return fee
    if base_asset and fee_asset == base_asset:
        return fee * float(fill.price or 0.0)
    return fee


def _split_symbol_assets(symbol: str) -> tuple[str, str]:
    normalized = symbol.strip().upper()
    if not normalized:
        return "", ""

    for delimiter in ("/", "-", "_"):
        if delimiter in normalized:
            parts = [part for part in normalized.split(delimiter) if part]
            if len(parts) >= 2:
                return parts[0], parts[1]

    known_quote_assets = (
        "USDT",
        "USDC",
        "BUSD",
        "USDP",
        "FDUSD",
        "DAI",
        "USD",
        "BTC",
        "ETH",
    )
    for quote in known_quote_assets:
        if normalized.endswith(quote) and len(normalized) > len(quote):
            return normalized[: -len(quote)], quote
    return normalized, ""


def _estimate_collateral_notional(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    symbol: str,
) -> float:
    rows = (
        with_tenant(db.query(AccountBalanceSnapshot.asset, AccountBalanceSnapshot.total), AccountBalanceSnapshot, user_id)
        .filter(AccountBalanceSnapshot.exchange_account_id == account_id)
        .all()
    )
    if not rows:
        return 0.0

    totals = {
        str(asset or "").upper(): max(float(total or 0.0), 0.0)
        for asset, total in rows
        if str(asset or "").strip()
    }
    if not totals:
        return 0.0

    _, quote_asset = _split_symbol_assets(symbol)
    quote_asset = quote_asset.upper()
    stable_assets = ("USDT", "USDC", "USD", "BUSD", "FDUSD", "USDP", "DAI", "TUSD")
    candidate_assets: list[str] = []
    if quote_asset:
        candidate_assets.append(quote_asset)
    if not quote_asset or quote_asset in stable_assets:
        candidate_assets.extend(stable_assets)

    collateral = sum(totals.get(asset, 0.0) for asset in dict.fromkeys(candidate_assets))
    if collateral > 0:
        return collateral

    # When the account only carries one collateral asset, using that single asset
    # is safer than falling back to a client-provided ratio.
    if len(totals) == 1:
        return next(iter(totals.values()))
    return 0.0


def _estimate_position_exposure_notional(
    db: Session,
    *,
    user_id: int,
    account_id: int,
) -> float:
    rows = with_tenant(db.query(PositionSnapshot), PositionSnapshot, user_id).filter(
        PositionSnapshot.exchange_account_id == account_id
    ).all()
    exposure = 0.0
    for row in rows:
        quantity = abs(float(row.quantity or 0.0))
        mark_price = float(row.mark_price or 0.0)
        entry_price = float(row.entry_price or 0.0)
        ref_price = mark_price if mark_price > 0 else entry_price
        if quantity <= 0 or ref_price <= 0:
            continue
        exposure += quantity * ref_price
    return exposure
