from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.config import BASE_DIR
from core.database import get_db
from core.security import verify_password, create_session_token, get_current_user_optional, init_default_accounts
from models import UserModel

router = APIRouter(tags=["Auth"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if user:
        return RedirectResponse(url="/shipping", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={})

@router.post("/api/login")
async def process_login(request: Request, db: Session = Depends(get_db)):
    # DB에 등록된 사용자가 한 명도 없다면 즉시 초기 계정(admin, user) 자동 생성
    if db.query(UserModel).count() == 0:
        init_default_accounts()

    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()

    user = db.query(UserModel).filter(UserModel.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=400, detail="아이디 또는 비밀번호가 일치하지 않습니다.")

    token = create_session_token(user.username)
    response = JSONResponse(content={"status": "success", "username": user.username, "role": user.role})
    response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax", max_age=86400 * 7)
    return response

@router.get("/logout")
async def process_logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="session_token")
    return response