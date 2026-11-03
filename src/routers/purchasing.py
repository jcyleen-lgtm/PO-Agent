"""Purchasing decisions: replenishment list and detail, demand plan, planning settings. HTTP only: parameters in, service call, response out."""

from fastapi import APIRouter

from src.services import purchasing_service as svc

router = APIRouter()


@router.get('/api/replenishment')
async def replenishment(product_type: str = 'import', only_reorder: bool = False, urgency: str = 'all'):
    return await svc.replenishment(product_type, only_reorder, urgency)


@router.get('/api/forecast/plan')
async def forecast_plan(product_type: str = 'import'):
    return await svc.forecast_plan(product_type)


@router.get('/api/replenishment/sku/{sku}')
async def replenishment_sku(sku: str):
    return await svc.replenishment_sku(sku)


@router.get('/api/settings')
async def get_planning_settings():
    return await svc.get_planning_settings()
