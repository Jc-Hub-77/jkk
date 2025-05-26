# backend/services/user_service.py
import datetime
import uuid
import logging # Add logging import
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import timedelta

from sqlalchemy.orm import Session
from sqlalchemy import or_
from ..models import User, Profile, Referral # Adjusted import path
from ..config import settings # Adjusted import path

import random
import string

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Helper Functions ---
import smtplib
from email.mime.text import MIMEText

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Initialize logger
logger = logging.getLogger(__name__)

# --- Helper Functions ---
import smtplib
from email.mime.text import MIMEText

# Email sending utility
# In a real app, this would ideally use a task queue (e.g., Celery) for asynchronous sending
# and a more robust email library or service SDK (e.g., SendGrid, Mailgun).
# This is a basic synchronous implementation using smtplib.
def send_email_async(to_email: str, subject: str, body: str):
    """
    Sends an email using SMTP. This is a basic synchronous implementation.
    For production, consider using a task queue and a dedicated email service.
    """
    if not settings.SMTP_HOST or not settings.SMTP_USER or not settings.SMTP_PASSWORD or not settings.EMAILS_FROM_EMAIL:
        logger.warning(f"Email not sent to {to_email}: SMTP settings are not fully configured.")
        logger.info(f"Simulating sending email to: {to_email}") # Use logger instead of print
        logger.info(f"Subject: {subject}") # Use logger instead of print
        logger.info(f"Body:\n{body}") # Use logger instead of print
        return {"status": "warning", "message": "Email settings not configured."}

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = settings.EMAILS_FROM_EMAIL
        msg['To'] = to_email

        # Use SMTP_PORT from settings, default to 587 if None
        smtp_port = settings.SMTP_PORT if settings.SMTP_PORT is not None else 587

        with smtplib.SMTP(settings.SMTP_HOST, smtp_port) as server:
            if settings.SMTP_TLS:
                server.starttls() # Secure the connection
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.EMAILS_FROM_EMAIL, to_email, msg.as_string())

        logger.info(f"Successfully sent email to {to_email} with subject '{subject}'.")
        return {"status": "success", "message": "Email sent successfully."}

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}", exc_info=True)
        return {"status": "error", "message": f"Failed to send email: {e}"}

def _generate_verification_token_data():
    token = str(uuid.uuid4())
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS)
    return token, expires_at

def _generate_unique_referral_code(db_session: Session) -> str:
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        existing_user = db_session.query(User).filter(User.referral_code == code).first()
        if not existing_user:
            return code

# --- Password Hashing ---
def _get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def _verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# --- Token Creation ---
def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

# --- User Service Functions ---

def get_user_by_id(db_session: Session, user_id: int) -> User | None:
    """
    Retrieves a user by their ID.
    """
    return db_session.query(User).filter(User.id == user_id).first()

def get_user_by_email(db_session: Session, email: str) -> User | None:
    """
    Retrieves a user by their email.
    """
    return db_session.query(User).filter(User.email == email).first()

def get_user_by_username(db_session: Session, username: str) -> User | None:
    """
    Retrieves a user by their username.
    """
    return db_session.query(User).filter(User.username == username).first()


def register_user(db_session: Session, email: str, username: str, password: str, referral_code_used: str = None):
    print(f"Registering user: {username}, email: {email}, referral code used: {referral_code_used}")

    if not email or "@" not in email or "." not in email:
        return {"status": "error", "message": "Invalid email format."}
    if not username or len(username) < 3:
        return {"status": "error", "message": "Username must be at least 3 characters."}
    if not password or len(password) < 8:
        return {"status": "error", "message": "Password must be at least 8 characters."}

    existing_user_by_email = get_user_by_email(db_session, email)
    if existing_user_by_email:
        return {"status": "error", "message": "Email already registered."}
    
    existing_user_by_username = get_user_by_username(db_session, username)
    if existing_user_by_username:
        return {"status": "error", "message": "Username already exists."}

    hashed_password = _get_password_hash(password)
    email_verification_token, email_verification_token_expires_at = _generate_verification_token_data()
    user_own_referral_code = _generate_unique_referral_code(db_session)

    referrer_user_id_to_store = None
    if referral_code_used:
        referrer = db_session.query(User).filter(User.referral_code == referral_code_used).first()
        if referrer:
            referrer_user_id_to_store = referrer.id
        else:
            print(f"Warning: Referral code '{referral_code_used}' provided by {username} is invalid or not found.")
            # Consider if this should be a hard error:
            # return {"status": "error", "message": "Invalid referral code provided."}

    new_user = User(
        username=username,
        email=email,
        password_hash=hashed_password,
        email_verified=False,
        is_admin=False,
        referral_code=user_own_referral_code,
        referred_by_user_id=referrer_user_id_to_store,
        created_at=datetime.datetime.utcnow(),
        email_verification_token=email_verification_token,
        email_verification_token_expires_at=email_verification_token_expires_at
    )
    new_profile = Profile(user=new_user)

    try:
        db_session.add(new_user)
        db_session.add(new_profile)
        db_session.flush() 

        if referrer_user_id_to_store and new_user.id:
            new_referral_record = Referral(
                referrer_user_id=referrer_user_id_to_store,
                referred_user_id=new_user.id,
                signed_up_at=new_user.created_at
            )
            db_session.add(new_referral_record)
        
        db_session.commit()
        db_session.refresh(new_user)
        if new_user.profile:
             db_session.refresh(new_user.profile)

        verification_link = f"{settings.FRONTEND_URL}/verify-email?token={email_verification_token}"
        email_subject = f"Verify your email for {settings.PROJECT_NAME}"
        email_body = (
            f"Hi {username},\n\n"
            f"Thanks for registering for {settings.PROJECT_NAME}! Please click the link below to verify your email address:\n"
            f"{verification_link}\n\n"
            f"This link will expire in {settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS} hours.\n\n"
            f"Thanks,\nThe {settings.PROJECT_NAME} Team"
        )
        send_email_async(new_user.email, email_subject, email_body)
        
        return {"status": "success", "message": "Registration successful. Please check your email for verification.", "user_id": new_user.id}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error during user registration: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": "Database error during registration."}


def verify_email(db_session: Session, verification_token: str):
    user_to_verify = db_session.query(User).filter(User.email_verification_token == verification_token).first()
    
    if not user_to_verify:
        return {"status": "error", "message": "Invalid verification token."}
    if user_to_verify.email_verified:
        return {"status": "info", "message": "Email already verified."}
    if user_to_verify.email_verification_token_expires_at < datetime.datetime.utcnow():
        return {"status": "error", "message": "Verification token has expired. Please request a new one."}

    user_to_verify.email_verified = True
    user_to_verify.email_verification_token = None
    user_to_verify.email_verification_token_expires_at = None
    try:
        db_session.commit()
        return {"status": "success", "message": "Email verified successfully."}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error during email verification commit: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": "Database error during email verification."}


def login_user(db_session: Session, username_or_email: str, password: str):
    user = db_session.query(User).filter(
        or_(User.username == username_or_email, User.email == username_or_email)
    ).first()

    if not user or not _verify_password(password, user.password_hash):
        return {"status": "error", "message": "Invalid username/email or password."}
    if not user.email_verified:
        return {"status": "error", "message": "Email not verified. Please verify your email before logging in."}
        
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username, "is_admin": user.is_admin},
        expires_delta=access_token_expires
    )
    
    return {
        "status": "success", 
        "access_token": access_token, 
        "token_type": "bearer",
        "user_id": user.id, 
        "username": user.username,
        "is_admin": user.is_admin
    }


def get_user_profile(db_session: Session, user_id: int):
    user = get_user_by_id(db_session, user_id)
    if not user:
        return {"status": "error", "message": "User not found."}
    
    profile_data = user.profile

    return {
        "status": "success",
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "is_admin": user.is_admin,
        "email_verified": user.email_verified,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "full_name": profile_data.full_name if profile_data else None,
        "bio": profile_data.bio if profile_data else None,
        "referral_code": user.referral_code,
    }

def update_user_profile(db_session: Session, user_id: int, data_to_update: dict):
    user = get_user_by_id(db_session, user_id)
    if not user:
        return {"status": "error", "message": "User not found."}

    profile_updated = False
    if not user.profile: 
        user.profile = Profile()
        db_session.add(user.profile) # Add to session if creating new
        profile_updated = True # Mark as updated because we are adding a profile

    if "email" in data_to_update and data_to_update["email"] != user.email:
        new_email = data_to_update["email"]
        if not new_email or "@" not in new_email or "." not in new_email:
             return {"status": "error", "message": "Invalid new email format."}
        
        existing_email_user = get_user_by_email(db_session, new_email)
        if existing_email_user and existing_email_user.id != user_id: # Check if email is used by another user
            return {"status": "error", "message": "New email address is already in use."}
        
        user.email = new_email
        user.email_verified = False
        token, expires_at = _generate_verification_token_data()
        user.email_verification_token = token
        user.email_verification_token_expires_at = expires_at
        
        verification_link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
        email_subject = f"Verify your new email address for {settings.PROJECT_NAME}"
        email_body = f"Hi {user.username},\n\nPlease click the link to verify your new email address: {verification_link}"
        send_email_async(user.email, email_subject, email_body)
        profile_updated = True

    if "full_name" in data_to_update and user.profile:
        if user.profile.full_name != data_to_update["full_name"]:
            user.profile.full_name = data_to_update["full_name"]
            profile_updated = True
    
    if "bio" in data_to_update and user.profile:
        if user.profile.bio != data_to_update["bio"]:
            user.profile.bio = data_to_update["bio"]
            profile_updated = True
    
    if not profile_updated: # If no actual changes were made
         return {"status": "info", "message": "No changes detected to update."}

    try:
        db_session.commit()
        return {"status": "success", "message": "Profile updated successfully."}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error during profile update commit: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": "Database error during profile update."}


def change_password(db_session: Session, user_id: int, old_password: str, new_password: str):
    user = get_user_by_id(db_session, user_id)
    if not user:
        return {"status": "error", "message": "User not found."}
    if not _verify_password(old_password, user.password_hash):
        return {"status": "error", "message": "Incorrect old password."}
    if len(new_password) < 8:
        return {"status": "error", "message": "New password must be at least 8 characters."}

    user.password_hash = _get_password_hash(new_password)
    try:
        db_session.commit()
        # TODO: Invalidate other active sessions/tokens for this user
        return {"status": "success", "message": "Password changed successfully."}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error during password change commit: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": "Database error during password change."}

# `manage_security_settings` placeholder
def manage_security_settings(user_id, settings_data): # Renamed 'settings' param to avoid conflict
    logger.info(f"Managing security settings for user_id: {user_id} with settings: {settings_data}") # Use logger instead of print
    return {"status": "success_placeholder", "message": "Security settings updated (placeholder)."}

def toggle_user_active_status(db_session: Session, user_id: int, is_active: bool):
    """
    Toggles the active status of a user.
    """
    user = get_user_by_id(db_session, user_id)
    if not user:
        return {"status": "error", "message": "User not found."}

    user.is_active = is_active
    try:
        db_session.commit()
        status_message = "activated" if is_active else "deactivated"
        logger.info(f"User ID {user_id} has been {status_message}.")
        return {"status": "success", "message": f"User {status_message} successfully."}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error toggling active status for user {user_id}: {e}", exc_info=True)
        return {"status": "error", "message": "Database error toggling user status."}


def forgot_password(db_session: Session, email: str):
    user = get_user_by_email(db_session, email)
    if not user:
        return {"status": "error", "message": "User with this email not found."}
    if user.email_verified:
        return {"status": "info", "message": "Email already verified."}

    email_verification_token, email_verification_token_expires_at = _generate_verification_token_data()
    user.email_verification_token = email_verification_token
    user.email_verification_token_expires_at = email_verification_token_expires_at

    try:
        db_session.commit()

        verification_link = f"{settings.FRONTEND_URL}/verify-email?token={email_verification_token}"
        email_subject = f"Verify your email for {settings.PROJECT_NAME}"
        email_body = (
            f"Hi {user.username},\n\n"
            f"Please click the link to verify your email address:\n"
            f"{verification_link}\n\n"
            f"This link will expire in {settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS} hours.\n\n"
            f"Thanks,\nThe {settings.PROJECT_NAME} Team"
        )
        send_email_async(user.email, email_subject, email_body)

        return {"status": "success", "message": "New verification email sent. Please check your inbox."}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error resending verification email: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": "Database error during resend verification email."}


# The __main__ block from user_management.py is removed as this is now a service module.
# Testing should be done via API endpoints or dedicated test scripts.
