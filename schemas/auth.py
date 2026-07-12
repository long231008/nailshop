from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):

    full_name: str

    email: EmailStr

    password: str



class VerifyOTPRequest(BaseModel):

    pending_id: str

    otp: str