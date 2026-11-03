"""Data imports: sales ETL (upload, confirm) and stock snapshot upload. HTTP only: parameters in, service call, response out."""

from fastapi import APIRouter, File, Form, UploadFile

from src.services import sales_service as svc

router = APIRouter()


@router.post('/api/etl/upload')
async def etl_upload(file: UploadFile = File(...)):
    return await svc.etl_upload(file.filename, await file.read())


@router.post('/api/etl/confirm/{file_id}')
async def etl_confirm(file_id: str):
    return await svc.etl_confirm(file_id)


@router.post('/api/stock/upload')
async def stock_upload(file: UploadFile = File(...), report_date: str = Form(None)):
    return await svc.stock_upload(file.filename, await file.read(), report_date)
