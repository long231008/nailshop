import uuid
from pathlib import Path

import cloudinary
import cloudinary.uploader
from fastapi import UploadFile

from app.shared.infrastructure.config.settings import settings

UPLOADS_DIR = Path(__file__).resolve().parents[3] / "uploads"

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
}


class CloudinaryStorage:
    def __init__(self):
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )

    def save(self, file: UploadFile) -> str:
        result = cloudinary.uploader.upload(
            file.file, folder="custom_designs", resource_type="image"
        )
        return result["secure_url"]


class LocalDiskStorage:
    """Design photos on the backend's own disk, served from /uploads.

    Used whenever Cloudinary credentials are absent, so the feature works out of
    the box in development.
    """

    def save(self, file: UploadFile) -> str:
        UPLOADS_DIR.mkdir(exist_ok=True)
        extension = _EXTENSIONS.get(file.content_type, ".img")
        filename = f"design-{uuid.uuid4().hex}{extension}"
        with open(UPLOADS_DIR / filename, "wb") as out:
            out.write(file.file.read())
        return f"{settings.BACKEND_PUBLIC_URL}/uploads/{filename}"


def _cloudinary_configured() -> bool:
    name = settings.CLOUDINARY_CLOUD_NAME
    return bool(name) and not name.startswith("your-") and name != "ci-test"


_storage = CloudinaryStorage() if _cloudinary_configured() else LocalDiskStorage()


def get_design_storage():
    return _storage
