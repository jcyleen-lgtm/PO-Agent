"""Purchase orders: upload, list, summary, edit, delete, receive-all. HTTP only: parameters in, service call, response out."""

from fastapi import APIRouter, File, UploadFile

from src.services import purchase_order_service as svc

router = APIRouter()


@router.post('/api/po/upload')
async def po_upload(file: UploadFile = File(...)):
    return await svc.po_upload(file.filename, await file.read())


@router.get('/api/po/list')
async def po_list(status: str = 'open'):
    return await svc.po_list(status)


@router.get('/api/po/summary')
async def po_summary():
    return await svc.po_summary()


@router.delete('/api/po/{po_id}')
async def po_delete(po_id: int):
    return await svc.po_delete(po_id)


@router.patch('/api/po/{po_id}')
async def po_update(po_id: int, body: dict = None):
    return await svc.po_update(po_id, body)


@router.post('/api/po/receive-all')
async def po_receive_all(body: dict = None):
    return await svc.po_receive_all(body)
