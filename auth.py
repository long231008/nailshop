from fastapi import FastAPI 
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr 
from uuid import uuid4 
import random 
import redis
from jose import jwt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import Depends
from database import get_db #file
from models import User #file

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"]
)

# Kết nối Redis
redis_client = redis.Redis(
    host="localhost",
    port=6379,
    db=0,
    decode_responses=True
)

# Request model
class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr
    password: str


@router.post("/register")
def register(request: RegisterRequest):

    # 1. Tạo Pending ID
    pending_id = str(uuid4())

    # 2. Sinh OTP 6 số
    otp = str(random.randint(100000, 999999))

    # 3. Lưu OTP vào Redis (TTL = 5 phút)
    redis_client.setex(
        f"otp:{pending_id}",
        300,
        otp
    )

    # 4. Lưu trạng thái Pending
    redis_client.setex(
        f"user:{pending_id}",
        300,
        request.model_dump_json()
    )

    # Sau này có thể gửi OTP qua Email hoặc SMS
    print("OTP:", otp)

    # 5. Trả về pending_id
    return {
        "message": "Account created in Pending status.",
        "pending_id": pending_id
    }




  
router = APIRouter(prefix="/api/auth")


class VerifyOTPRequest(BaseModel):
    pending_id: str
    otp: str


@router.post("/verify-otp")
def verify_otp(request: VerifyOTPRequest):

    # Lấy OTP trong Redis
    saved_otp = redis_client.get(f"otp:{request.pending_id}")

    if saved_otp is None:
        raise HTTPException(
            status_code=400,
            detail="OTP expired."
        )

    if saved_otp != request.otp:
        raise HTTPException(
            status_code=400,
            detail="Invalid OTP."
        )

    # lấy user pending trong database
    def verify_otp(
        request: VerifyOTPRequest,
        db: Session = Depends(get_db)
    ):

    # Lấy user đang ở trạng thái Pending
        user = (
            db.query(User)
            .filter(
                User.pending_id == request.pending_id,
                User.is_active == False
            )
            .first()
        )

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="Pending account not found."
            )

    # lấy user trong database  
    user = (
        db.query(User)
        .filter(User.pending_id == request.pending_id)
        .first()
    )

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Kích hoạt tài khoản
    user.is_active = True

    # Lưu vào database
    db.commit()

    # Cập nhật object
    db.refresh(user)

    # Xóa OTP
    redis_client.delete(f"otp:{request.pending_id}")

    # JWT Token
    SECRET_KEY = "your-secret-key"
    ALGORITHM = "HS256"

    payload = {
        "user_id": user.id,
        "role": user.role,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }

    token = jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    access_token = token

    return {
        "message": "Account activated successfully.",
        "access_token": access_token,
        "token_type": "bearer"
    }