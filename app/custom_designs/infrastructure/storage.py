import uuid
from pathlib import Path

from fastapi import UploadFile

UPLOAD_DIR = Path("uploads/custom_designs")


class LocalFileStorage:
    def save(self, file: UploadFile) -> str:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        extension = Path(file.filename or "").suffix
        filename = f"{uuid.uuid4()}{extension}"
        destination = UPLOAD_DIR / filename
        with open(destination, "wb") as buffer:
            buffer.write(file.file.read())
        return f"/uploads/custom_designs/{filename}"
