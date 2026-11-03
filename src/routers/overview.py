"""Overview, database stats and weekly analysis. HTTP only: parameters in, service call, response out."""

from fastapi import APIRouter

from src.services import overview_service as svc

router = APIRouter()


@router.get('/api/db/stats')
async def db_stats():
    return await svc.db_stats()


@router.get('/api/overview')
async def overview_summary(start: str = None, end: str = None, preset: str = 'l4w', top_sort: str = 'revenue', product_type: str = 'all'):
    return await svc.overview_summary(start, end, preset, top_sort, product_type)


@router.get('/api/weekly-analysis')
async def weekly_analysis(product_type: str = 'import', scope: str = 'all'):
    return await svc.weekly_analysis(product_type, scope)
