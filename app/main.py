
from os import environ

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.login import router as login_router
from app.api.logout import router as logout_router
from app.api.me import router as me_router
from app.api.refresh import router as refresh_router
from app.api.schedules import router as schedules_router
from app.api.password_reset import router as password_reset_router

app = FastAPI(title="Secretary Schedule API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in environ.get(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router)
app.include_router(login_router)
app.include_router(logout_router)
app.include_router(me_router)
app.include_router(refresh_router)
app.include_router(schedules_router)
app.include_router(password_reset_router)

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
