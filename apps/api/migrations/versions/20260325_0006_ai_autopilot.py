"""add ai autopilot tables

Revision ID: 20260325_0006
Revises: 20260324_0005
Create Date: 2026-03-25 16:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260325_0006"
down_revision = "20260324_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_provider_credentials_id", "ai_provider_credentials", ["id"])
    op.create_index("ix_ai_provider_credentials_user_id", "ai_provider_credentials", ["user_id"])

    op.create_table(
        "ai_autopilot_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("ai_provider_credentials.id"), nullable=False),
        sa.Column("exchange_account_id", sa.Integer(), sa.ForeignKey("exchange_accounts.id"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("interval", sa.String(length=16), nullable=False),
        sa.Column("strategy_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="disabled"),
        sa.Column("execution_mode", sa.String(length=16), nullable=False, server_default="dry_run"),
        sa.Column("decision_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("minimum_confidence", sa.Numeric(6, 4), nullable=False, server_default="0.6000"),
        sa.Column("max_actions_per_hour", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("custom_prompt", sa.Text(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_decision_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_autopilot_policies_id", "ai_autopilot_policies", ["id"])
    op.create_index("ix_ai_autopilot_policies_user_id", "ai_autopilot_policies", ["user_id"])
    op.create_index("ix_ai_autopilot_policies_provider_id", "ai_autopilot_policies", ["provider_id"])
    op.create_index(
        "ix_ai_autopilot_policies_exchange_account_id",
        "ai_autopilot_policies",
        ["exchange_account_id"],
    )

    op.create_table(
        "ai_autopilot_decision_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("policy_id", sa.Integer(), sa.ForeignKey("ai_autopilot_policies.id"), nullable=False),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("ai_provider_credentials.id"), nullable=False),
        sa.Column("exchange_account_id", sa.Integer(), sa.ForeignKey("exchange_accounts.id"), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False, server_default="0.0000"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("factors_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("context_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("raw_response_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("execution_result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_autopilot_decision_runs_id", "ai_autopilot_decision_runs", ["id"])
    op.create_index("ix_ai_autopilot_decision_runs_user_id", "ai_autopilot_decision_runs", ["user_id"])
    op.create_index("ix_ai_autopilot_decision_runs_policy_id", "ai_autopilot_decision_runs", ["policy_id"])
    op.create_index("ix_ai_autopilot_decision_runs_provider_id", "ai_autopilot_decision_runs", ["provider_id"])
    op.create_index(
        "ix_ai_autopilot_decision_runs_exchange_account_id",
        "ai_autopilot_decision_runs",
        ["exchange_account_id"],
    )
    op.create_index(
        "ix_ai_autopilot_decision_runs_created_at",
        "ai_autopilot_decision_runs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_autopilot_decision_runs_created_at", table_name="ai_autopilot_decision_runs")
    op.drop_index("ix_ai_autopilot_decision_runs_exchange_account_id", table_name="ai_autopilot_decision_runs")
    op.drop_index("ix_ai_autopilot_decision_runs_provider_id", table_name="ai_autopilot_decision_runs")
    op.drop_index("ix_ai_autopilot_decision_runs_policy_id", table_name="ai_autopilot_decision_runs")
    op.drop_index("ix_ai_autopilot_decision_runs_user_id", table_name="ai_autopilot_decision_runs")
    op.drop_index("ix_ai_autopilot_decision_runs_id", table_name="ai_autopilot_decision_runs")
    op.drop_table("ai_autopilot_decision_runs")

    op.drop_index("ix_ai_autopilot_policies_exchange_account_id", table_name="ai_autopilot_policies")
    op.drop_index("ix_ai_autopilot_policies_provider_id", table_name="ai_autopilot_policies")
    op.drop_index("ix_ai_autopilot_policies_user_id", table_name="ai_autopilot_policies")
    op.drop_index("ix_ai_autopilot_policies_id", table_name="ai_autopilot_policies")
    op.drop_table("ai_autopilot_policies")

    op.drop_index("ix_ai_provider_credentials_user_id", table_name="ai_provider_credentials")
    op.drop_index("ix_ai_provider_credentials_id", table_name="ai_provider_credentials")
    op.drop_table("ai_provider_credentials")
