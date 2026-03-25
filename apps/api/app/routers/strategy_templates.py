from __future__ import annotations

from fastapi import APIRouter

from ..schemas import StrategyTemplateResponse
from ..services.strategy_templates import list_strategy_templates, serialize_strategy_template

router = APIRouter(prefix="/api/strategy-templates", tags=["strategy-templates"])


@router.get("", response_model=list[StrategyTemplateResponse])
def get_strategy_templates():
    return [serialize_strategy_template(item) for item in list_strategy_templates(featured_only=True)]
