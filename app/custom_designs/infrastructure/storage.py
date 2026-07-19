import cloudinary
import cloudinary.uploader
from fastapi import UploadFile

from app.shared.infrastructure.config.settings import settings


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


_storage = CloudinaryStorage()


def get_design_storage() -> CloudinaryStorage:
    return _storage
