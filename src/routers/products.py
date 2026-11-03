"""Product lists: Hero SKU, best-seller scope, Master SKU, Food SKU, product history. HTTP only: parameters in, service call, response out."""

from fastapi import APIRouter, File, UploadFile

from src.services import product_service as svc

router = APIRouter()


@router.post('/api/hero-sku/upload')
async def upload_hero_sku(file: UploadFile = File(...)):
    return await svc.upload_hero_sku(file.filename, await file.read())


@router.get('/api/bestseller/scope')
async def bestseller_scope():
    return await svc.bestseller_scope()


@router.post('/api/master-sku/upload')
async def upload_master_sku(file: UploadFile = File(...)):
    return await svc.upload_master_sku(file.filename, await file.read())


@router.post('/api/food-sku/upload')
async def upload_food_sku(file: UploadFile = File(...)):
    return await svc.upload_food_sku(file.filename, await file.read())


@router.get('/api/product/{sku}/history')
async def product_history(sku: str, weeks: int = 52):
    return await svc.product_history(sku, weeks)
