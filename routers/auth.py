from urllib.parse import urlparse

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.config import BASE_DIR, SESSION_COOKIE_MAX_AGE_SECONDS, SESSION_COOKIE_SECURE
from core.database import get_db
from core.security import verify_password, create_session_token, get_current_user_optional, init_default_accounts
from models import UserModel
from services.auth_session_service import revoke_session, session_status, touch_session

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

    if hasattr(user, "is_active") and not user.is_active:
        raise HTTPException(status_code=400, detail="아이디 또는 비밀번호가 일치하지 않습니다.")
    revoke_session(db, request.cookies.get("session_token"))
    token = create_session_token(user.username, db)
    db.commit()
    response = JSONResponse(content={"status": "success", "username": user.username, "role": user.role})
    response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax", max_age=SESSION_COOKIE_MAX_AGE_SECONDS, secure=SESSION_COOKIE_SECURE)
    return response

@router.get("/logout")
async def process_logout(request: Request, db: Session = Depends(get_db)):
    revoke_session(db, request.cookies.get("session_token"))
    db.commit()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="session_token")
    return response

@router.get("/api/session")
def get_session_status(request: Request, db: Session = Depends(get_db)):
    # 이 조회는 활동시간을 갱신하지 않습니다.
    data = session_status(db, request.cookies.get("session_token"))
    return JSONResponse(content=data, headers={"Cache-Control": "private, no-store"})


@router.post("/api/session/activity")
def record_session_activity(request: Request, db: Session = Depends(get_db)):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and urlparse(origin).netloc.lower() != request.headers.get("host", "").lower()
    ):
        raise HTTPException(403, "외부 사이트에서 로그인 시간을 연장할 수 없습니다.")
    data = touch_session(db, request.cookies.get("session_token"),
                         request.headers.get("x-mes-session-activity"))
    return JSONResponse(content=data, headers={"Cache-Control": "private, no-store"})
