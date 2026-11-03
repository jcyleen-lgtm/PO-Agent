"""Inventory health: SKU list, summary, best sellers at risk, legacy coverage. HTTP only: parameters in, service call, response out."""

from fastapi import APIRouter

from src.services import inventory_service as svc

router = APIRouter()


@router.get('/api/best-sellers-at-risk')
async def best_sellers_at_risk(product_type: str = 'all', limit: int = 50):
    return await svc.best_sellers_at_risk(product_type, limit)


@router.get('/api/coverage')
async def coverage_analysis():
    return await svc.coverage_analysis()


@router.get('/api/inventory')
async def inventory_analysis(sort: str = 'priority', product_type: str = 'import', scope: str = 'all', summary_only: bool = False):
    return await svc.inventory_analysis(sort, product_type, scope, summary_only)


@router.get('/api/inventory/summary')
async def inventory_summary(product_type: str = 'import', scope: str = 'all'):
    return await svc.inventory_summary(product_type, scope)
