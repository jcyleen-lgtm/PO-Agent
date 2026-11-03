"""Holt's DES forecast: run and read results. HTTP only: parameters in, service call, response out."""

from fastapi import APIRouter

from src.services import forecast_service as svc

router = APIRouter()


@router.post('/api/forecast/run')
async def forecast_run():
    return await svc.forecast_run()


@router.get('/api/forecast/results')
async def forecast_results(limit: int = 200, sort: str = 'volume', fields: str = 'full'):
    return await svc.forecast_results(limit, sort, fields)


@router.get('/api/forecast/sku/{sku}')
async def forecast_sku(sku: str):
    return await svc.forecast_sku(sku)
