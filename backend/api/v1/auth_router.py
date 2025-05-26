# backend/api/v1/auth_router.py
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from ...schemas import user_schemas
from ...services import user_service # Renamed user_management to user_service for clarity
from ...models import User
from ...config import settings
from ...db import get_db # Changed to import from backend.db

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login") # Points to our login endpoint

async def get_current_user(
    token: str = Depends(oauth2_scheme), 
    db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id_str: str = payload.get("sub") # "sub" should contain the user_id as a string
        if user_id_str is None:
            raise credentials_exception
        
        # Ensure user_id_str can be converted to int
        try:
            user_id = int(user_id_str)
        except ValueError:
            raise credentials_exception

        token_data = user_schemas.TokenData(user_id=user_id, username=payload.get("username"))
    except JWTError:
        raise credentials_exception
    
    user = user_service.get_user_by_id(db, user_id=token_data.user_id)
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.email_verified or not current_user.is_active: # Check both email verification and active status
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user, email not verified, or account deactivated")
    return current_user

# --- Authentication Endpoints ---
@router.post("/register", response_model=user_schemas.UserRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_new_user(
    user_in: user_schemas.UserCreate, 
    db: Session = Depends(get_db)
):
    result = user_service.register_user(
        db_session=db,
        email=user_in.email,
        username=user_in.username,
        password=user_in.password,
        referral_code_used=user_in.referral_code_used
    )
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.post("/login", response_model=user_schemas.UserLoginResponse)
async def login_for_access_token(
    response: Response, # To set cookie if needed in future
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db: Session = Depends(get_db)
):
    # OAuth2PasswordRequestForm uses 'username' and 'password' fields
    result = user_service.login_user(
        db_session=db, 
        username_or_email=form_data.username, # form_data.username is the username_or_email
        password=form_data.password
    )
    if result["status"] == "error":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, # Correct status for login failure
            detail=result["message"],
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Example: Set token in an HttpOnly cookie (more secure for web browsers)
    # response.set_cookie(
    #     key="access_token", 
    #     value=f"Bearer {result['access_token']}", 
    #     httponly=True, 
    #     max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, 
    #     samesite="Lax", # or "Strict"
    #     secure=True # In production (HTTPS)
    # )
    return result


@router.get("/verify-email", response_model=user_schemas.GeneralResponse)
async def verify_user_email(token: str, db: Session = Depends(get_db)):
    result = user_service.verify_email(db_session=db, verification_token=token)
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

# --- User Profile Endpoints (Protected) ---
@router.get("/users/me", response_model=user_schemas.UserProfileResponse)
async def read_users_me(current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    # user_service.get_user_profile expects user_id as int
    profile_data = user_service.get_user_profile(db_session=db, user_id=current_user.id)
    if profile_data["status"] == "error":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=profile_data["message"])
    return profile_data

@router.put("/users/me", response_model=user_schemas.GeneralResponse)
async def update_current_user_profile(
    profile_update: user_schemas.ProfileUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    # Convert Pydantic model to dict for the service function
    update_data_dict = profile_update.dict(exclude_unset=True) 
    if not update_data_dict: # If nothing to update
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No update data provided.")

    result = user_service.update_user_profile(
        db_session=db,
        user_id=current_user.id,
        data_to_update=update_data_dict
    )
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.post("/users/me/password", response_model=user_schemas.GeneralResponse)
async def change_current_user_password(
    password_data: user_schemas.PasswordChange,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    result = user_service.change_password(
        db_session=db,
        user_id=current_user.id,
        old_password=password_data.old_password,
        new_password=password_data.new_password
    )
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.post("/resend-verification-email", response_model=user_schemas.GeneralResponse)
async def resend_verification_email(email: str, db: Session = Depends(get_db)):
    """
    Resends the email verification token to the user.
    """
    result = user_service.resend_verification_email(db_session=db, email=email)
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.post("/forgot-password", response_model=user_schemas.GeneralResponse)
async def forgot_password(email: str, db: Session = Depends(get_db)):
    result = user_service.forgot_password(db_session=db, email=email)
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.post("/request-password-reset", response_model=user_schemas.GeneralResponse)
async def request_password_reset(email: str, db: Session = Depends(get_db)):
    """
    Requests a password reset token to be sent to the user's email.
    """
    result = user_service.request_password_reset(db_session=db, email=email)
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.post("/reset-password", response_model=user_schemas.GeneralResponse)
async def reset_password(
    reset_data: user_schemas.PasswordReset,
    db: Session = Depends(get_db)
):
    """
    Resets the user's password using a valid token.
    """
    result = user_service.reset_password(
        db_session=db,
        token=reset_data.token,
        new_password=reset_data.new_password
    )
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

# Placeholder for a simple logout (if using cookies, it would clear the cookie)
# If using JWT in Authorization header, client just deletes the token.
@router.post("/logout")
async def logout(response: Response): # Pass response to clear cookie
    # response.delete_cookie("access_token")
    return {"status": "success", "message": "Logged out successfully (client should clear token)"}
