import re

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.deps import get_database
from app.services.errors import NotFoundError, RequestError
from app.services.storage import read_image
from app.services.themes import add_inventory_image, delete_inventory_image, list_inventory_images

router = APIRouter(prefix="/api/inventory", tags=["inventory-images"])
INVENTORY_ID = re.compile(r"^PROP-\d+$")
IMAGE_ID = re.compile(r"^IMG-\d+$")


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, RequestError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("/{inventory_id}/images")
async def get_images(inventory_id: str, request: Request) -> dict:
    if not INVENTORY_ID.fullmatch(inventory_id):
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return {"items": await list_inventory_images(await get_database(request), inventory_id)}


@router.post("/{inventory_id}/images", status_code=201)
async def upload_image(
    inventory_id: str,
    request: Request,
    file: UploadFile = File(...),
) -> dict:
    if not INVENTORY_ID.fullmatch(inventory_id):
        raise HTTPException(status_code=404, detail="Inventory item not found")
    data, content_type = await read_image(file)
    try:
        return await add_inventory_image(
            await get_database(request),
            inventory_id,
            data,
            content_type,
            file.filename or "image",
        )
    except (NotFoundError, RequestError) as exc:
        raise _map_error(exc) from exc


@router.delete("/{inventory_id}/images/{image_id}", status_code=204)
async def remove_image(inventory_id: str, image_id: str, request: Request) -> None:
    if not INVENTORY_ID.fullmatch(inventory_id) or not IMAGE_ID.fullmatch(image_id):
        raise HTTPException(status_code=404, detail="Inventory image not found")
    try:
        await delete_inventory_image(await get_database(request), inventory_id, image_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
