from pydantic import BaseModel, EmailStr, Field


class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(max_length=255)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=8, max_length=128)


class GenericMessage(BaseModel):
    message: str
