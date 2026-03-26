from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user_optional
from ..models import AiAutopilotPolicy, AiProviderCredential, ExchangeAccount, Strategy, User
from ..schemas import (
    WorkflowAiReadiness,
    WorkflowExchangeAccountSummary,
    WorkflowExchangeCoverage,
    WorkflowLiveTemplateItem,
    WorkflowReadinessAction,
    WorkflowReadinessResponse,
)
from ..services.risk_service import RiskService
from ..services.strategy_templates import list_strategy_templates
from ..tenant import with_tenant

router = APIRouter(prefix="/api/workflow", tags=["workflow"])
risk_service = RiskService()

_LIVE_STRATEGY_TYPES = {"grid", "futures_grid", "dca", "combo_grid_dca"}
_RUNNING_STATES = {"running", "starting"}


@router.get("/readiness", response_model=WorkflowReadinessResponse)
def get_workflow_readiness(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    live_templates = _list_live_supported_templates()
    if current_user is None:
        return WorkflowReadinessResponse(
            authenticated=False,
            live_supported_templates=live_templates,
            next_required_actions=[
                WorkflowReadinessAction(
                    code="sign_in",
                    label="Sign in or register",
                    path="/auth",
                    description="Create a session before using private accounts, strategies, and AI controls.",
                )
            ],
        )

    user_id = int(current_user.id)
    enrollment_required = not bool(current_user.totp_secret_encrypted)

    accounts = with_tenant(db.query(ExchangeAccount), ExchangeAccount, user_id).all()
    account_summary = _build_account_summary(accounts)
    has_risk_rule = risk_service.has_configured_rule(db, user_id=user_id)

    provider_count = with_tenant(db.query(AiProviderCredential), AiProviderCredential, user_id).count()
    policies_query = with_tenant(db.query(AiAutopilotPolicy), AiAutopilotPolicy, user_id)
    policy_count = policies_query.count()
    auto_enabled_count = policies_query.filter(
        AiAutopilotPolicy.status == "enabled",
        AiAutopilotPolicy.execution_mode == "auto",
    ).count()

    strategy_query = with_tenant(db.query(Strategy), Strategy, user_id)
    strategy_instances_total = strategy_query.count()
    live_strategy_instances_total = strategy_query.filter(Strategy.strategy_type.in_(_LIVE_STRATEGY_TYPES)).count()
    running_live_strategy_instances_total = strategy_query.filter(
        Strategy.strategy_type.in_(_LIVE_STRATEGY_TYPES),
        Strategy.status.in_(_RUNNING_STATES),
    ).count()

    next_required_actions = _build_next_actions(
        enrollment_required=enrollment_required,
        has_exchange_accounts=account_summary.total > 0,
        has_risk_rule=has_risk_rule,
        has_live_strategy=live_strategy_instances_total > 0,
        has_running_live_strategy=running_live_strategy_instances_total > 0,
        provider_count=provider_count,
        policy_count=policy_count,
        auto_enabled_count=auto_enabled_count,
    )

    return WorkflowReadinessResponse(
        authenticated=True,
        enrollment_required=enrollment_required,
        has_risk_rule=has_risk_rule,
        exchange_accounts_summary=account_summary,
        live_supported_templates=live_templates,
        ai_ready=WorkflowAiReadiness(
            provider_count=provider_count,
            policy_count=policy_count,
            auto_enabled_count=auto_enabled_count,
        ),
        next_required_actions=next_required_actions,
        strategy_instances_total=strategy_instances_total,
        live_strategy_instances_total=live_strategy_instances_total,
        running_live_strategy_instances_total=running_live_strategy_instances_total,
    )


def _list_live_supported_templates() -> list[WorkflowLiveTemplateItem]:
    templates = [
        item
        for item in list_strategy_templates(featured_only=True)
        if item.live_supported
    ]
    return [
        WorkflowLiveTemplateItem(template_key=item.template_key, display_name=item.display_name)
        for item in templates
    ]


def _build_account_summary(accounts: list[ExchangeAccount]) -> WorkflowExchangeAccountSummary:
    summary = WorkflowExchangeAccountSummary()
    by_exchange: dict[str, WorkflowExchangeCoverage] = {}

    for account in accounts:
        summary.total += 1
        if bool(account.is_testnet):
            summary.testnet += 1
        else:
            summary.live += 1

        exchange_key = str(account.exchange or "").strip().lower() or "unknown"
        coverage = by_exchange.setdefault(exchange_key, WorkflowExchangeCoverage())
        coverage.total += 1
        if bool(account.is_testnet):
            coverage.testnet += 1
        else:
            coverage.live += 1

    summary.by_exchange = {
        key: by_exchange[key]
        for key in sorted(by_exchange.keys())
    }
    return summary


def _build_next_actions(
    *,
    enrollment_required: bool,
    has_exchange_accounts: bool,
    has_risk_rule: bool,
    has_live_strategy: bool,
    has_running_live_strategy: bool,
    provider_count: int,
    policy_count: int,
    auto_enabled_count: int,
) -> list[WorkflowReadinessAction]:
    actions: list[WorkflowReadinessAction] = []

    if enrollment_required:
        actions.append(
            WorkflowReadinessAction(
                code="enroll_2fa",
                label="Complete Google Authenticator binding",
                path="/auth/enroll-2fa",
                description="Protected pages stay blocked until 2FA enrollment is complete.",
            )
        )

    if not has_exchange_accounts:
        actions.append(
            WorkflowReadinessAction(
                code="add_exchange_account",
                label="Add exchange account",
                path="/accounts",
                description="Create at least one exchange route before live strategy start.",
            )
        )

    if not has_risk_rule:
        actions.append(
            WorkflowReadinessAction(
                code="configure_risk_rule",
                label="Configure risk guardrails",
                path="/settings",
                description="Live runtime remains fail-closed until a risk rule is saved.",
            )
        )

    if not has_live_strategy:
        actions.append(
            WorkflowReadinessAction(
                code="create_strategy",
                label="Create first live-supported strategy",
                path="/strategies",
                description="Use spot_grid, futures_grid, dca, or combo_grid_dca as a starting point.",
            )
        )

    if provider_count <= 0:
        actions.append(
            WorkflowReadinessAction(
                code="create_ai_provider",
                label="Create AI provider",
                path="/ai",
                description="Register one OpenAI-compatible provider endpoint first.",
            )
        )

    if policy_count <= 0:
        actions.append(
            WorkflowReadinessAction(
                code="create_ai_policy",
                label="Create AI policy",
                path="/ai",
                description="Bind account, symbol, interval, and candidate strategies before dry-run.",
            )
        )

    if has_exchange_accounts and has_risk_rule and has_live_strategy and not has_running_live_strategy:
        actions.append(
            WorkflowReadinessAction(
                code="start_strategy",
                label="Start one strategy instance",
                path="/strategies",
                description="Issue a step-up token, then start a live-supported strategy.",
            )
        )

    if has_risk_rule and policy_count > 0 and auto_enabled_count <= 0:
        actions.append(
            WorkflowReadinessAction(
                code="enable_ai_auto",
                label="Enable one auto AI policy",
                path="/ai",
                description="Auto mode stays disabled until you explicitly enable a policy.",
            )
        )

    if not actions:
        actions.append(
            WorkflowReadinessAction(
                code="review_ops",
                label="Review Ops health",
                path="/ops",
                description="Core workflow prerequisites are complete. Monitor runtime health next.",
            )
        )

    return actions
