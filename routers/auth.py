from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from uuid import uuid4

from database import get_db
from models import User

from schemas.auth import (
    RegisterRequest,
    VerifyOTPRequest
)

from services.redis_service import (
    save_otp,
    get_otp,
    delete_otp,
    save_pending_user
)

from services.otp_service import generate_otp

from services.jwt_service import create_access_token



router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"]
)



@router.post("/register")
def register(
    request: RegisterRequest
):

    pending_id = str(uuid4())


    otp = generate_otp()


    save_otp(
        pending_id,
        otp
    )


    save_pending_user(
        pending_id,
        request.model_dump_json()
    )


    print(
        "OTP:",
        otp
    )


    return {

        "message":
        "Account created in Pending status.",

        "pending_id":
        pending_id
    }



@router.post("/verify-otp")
def verify_otp(
    request: VerifyOTPRequest,
    db: Session = Depends(get_db)
):

    saved_otp = get_otp(
        request.pending_id
    )


    if saved_otp is None:

        raise HTTPException(
            400,
            "OTP expired."
        )


    if saved_otp != request.otp:

        raise HTTPException(
            400,
            "Invalid OTP."
        )


    user = (

        db.query(User)

        .filter(
            User.pending_id
            ==
            request.pending_id
        )

        .first()

    )


    if user is None:

        raise HTTPException(
            404,
            "User not found"
        )


    user.is_active = True


    db.commit()

    db.refresh(user)


    delete_otp(
        request.pending_id
    )


    token = create_access_token(
        user.id,
        user.role
    )


    return {

        "message":
        "Account activated successfully.",

        "access_token":
        token,

        "token_type":
        "bearer"
    }