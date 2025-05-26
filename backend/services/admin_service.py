# backend/services/admin_service.py
import datetime
import os
import logging # Add logging import
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_ # For count, desc, or_
import sqlalchemy # Move sqlalchemy import to the top
import sqlalchemy.types # Add sqlalchemy.types import
import json
from backend.models import User, Strategy, UserStrategySubscription, PaymentTransaction, ApiKey # Adjusted import path
from backend.config import settings # Adjusted import path

# Initialize logger
logger = logging.getLogger(__name__)

# --- Admin User Management ---
def list_all_users(db_session: Session, page: int = 1, per_page: int = 20, search_term: str = None, sort_by: str = "id", sort_order: str = "asc"):
    """Lists all users with pagination, search, and sorting."""
    query = db_session.query(User)
    
    if search_term:
        search_filter = f"%{search_term}%"
        query = query.filter(
            or_(
                User.username.ilike(search_filter), 
                User.email.ilike(search_filter),
                User.id.cast(sqlalchemy.types.String).ilike(search_filter) # Search by ID
            )
        )
    
    # Sorting
    sort_column = getattr(User, sort_by, User.id) # Default to User.id if sort_by is invalid
    if sort_order.lower() == "desc":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(sort_column) # Default to asc

    total_users = query.count()
    users_data = query.offset((page - 1) * per_page).limit(per_page).all()
    
    return {
        "status": "success",
        "users": [{
            "id": u.id, "username": u.username, "email": u.email, 
            "is_admin": u.is_admin, 
            "is_active": u.is_active, # Include is_active field
            "email_verified": u.email_verified,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "profile_full_name": u.profile.full_name if u.profile else None # Example of fetching from related model
        } for u in users_data],
        "total_users": total_users,
        "page": page,
        "per_page": per_page,
        "total_pages": (total_users + per_page - 1) // per_page if per_page > 0 else 0
    }

def set_user_admin_status(db_session: Session, user_id: int, make_admin: bool):
    user = db_session.query(User).filter(User.id == user_id).first()
    if not user:
        return {"status": "error", "message": "User not found."}
    
    # Basic check: prevent removing the last admin if there's a concept of it.
    # This logic might need to be more sophisticated (e.g. superadmin role)
    if not make_admin and user.is_admin:
        admin_count = db_session.query(User).filter(User.is_admin == True).count()
        if admin_count <= 1:
            return {"status": "error", "message": "Cannot remove the last admin account."}

    user.is_admin = make_admin
    try:
        db_session.commit()
        logger.info(f"Admin: User {user_id} admin status set to {make_admin}.") # Use logger instead of print
        return {"status": "success", "message": f"User {user_id} admin status updated to {make_admin}."}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error setting admin status for user {user_id}: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": f"Database error: {e}"}

# Assuming User model has an 'is_active' field. If not, this function needs adjustment or removal.
def toggle_user_active_status(db_session: Session, user_id: int, activate: bool):
    """Toggles the active status of a user."""
    user = db_session.query(User).filter(User.id == user_id).first()
    if not user:
        logger.warning(f"Admin: Attempted to toggle active status for non-existent user ID: {user_id}")
        return {"status": "error", "message": "User not found."}

    user.is_active = activate

    try:
        db_session.commit()
        status_message = "activated" if activate else "deactivated"
        logger.info(f"Admin: User {user_id} has been {status_message}.")
        return {"status": "success", "message": f"User {user_id} {status_message} successfully."}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Admin: Error toggling active status for user {user_id}: {e}", exc_info=True)
        return {"status": "error", "message": f"Database error: {e}"}


# --- Admin Strategy Management ---
def list_all_strategies_admin(db_session: Session):
    # This will depend on how strategies are stored (DB vs. files in 'strategies/' dir)
    # If DB:
    try:
        strategies = db_session.query(Strategy).order_by(Strategy.name).all()
        return {
            "status": "success", 
            "strategies": [
                {
                    "id": s.id, "name": s.name, "description": s.description, 
                    "python_code_path": s.python_code_path, 
                    "default_parameters": s.default_parameters,
                    "category": s.category, "risk_level": s.risk_level,
                    "is_active": s.is_active,
                    "created_at": s.created_at.isoformat() if s.created_at else None
                } for s in strategies
            ]
        }
    except Exception as e:
        logger.error(f"Error listing strategies for admin: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": "Could not retrieve strategies."}


def add_new_strategy_admin(db_session: Session, name: str, description: str, python_code_path: str, 
                           default_parameters: str, category: str, risk_level: str):
    existing_strategy = db_session.query(Strategy).filter(Strategy.name == name).first()
    if existing_strategy:
        return {"status": "error", "message": f"Strategy with name '{name}' already exists."}
    
    # Validate python_code_path exists and is a valid strategy file.
    # Assuming strategies are stored in a directory specified by settings.STRATEGIES_DIR
    full_path = os.path.join(settings.STRATEGIES_DIR, python_code_path)
    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        logger.warning(f"Admin: Attempted to add strategy with invalid path: {python_code_path}")
        return {"status": "error", "message": f"Strategy file not found at path: {python_code_path}"}
    # TODO: Add more sophisticated validation for strategy file content (e.g., check for required class/methods)

    # Validate default_parameters is valid JSON
    try:
        json.loads(default_parameters)
    except json.JSONDecodeError:
        logger.warning(f"Admin: Attempted to add strategy with invalid JSON parameters: {default_parameters}")
        return {"status": "error", "message": "Default parameters must be valid JSON."}

    new_strategy = Strategy(
        name=name, 
        description=description, 
        python_code_path=python_code_path,
        default_parameters=default_parameters,
        category=category,
        risk_level=risk_level,
        is_active=True # New strategies are active by default
    )
    try:
        db_session.add(new_strategy)
        db_session.commit()
        db_session.refresh(new_strategy)
        return {"status": "success", "message": "Strategy added successfully.", "strategy_id": new_strategy.id}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error adding new strategy '{name}': {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": f"Database error while adding strategy: {e}"}

def update_strategy_admin(db_session: Session, strategy_id: int, updates: dict):
    strategy = db_session.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"status": "error", "message": "Strategy not found."}

    allowed_fields = ["name", "description", "python_code_path", "default_parameters", "category", "risk_level", "is_active"]
    updated_count = 0
    for key, value in updates.items():
        if key in allowed_fields:
            # Special handling for name uniqueness if changed
            if key == "name" and value != strategy.name:
                existing_strategy = db_session.query(Strategy).filter(Strategy.name == value, Strategy.id != strategy_id).first()
                if existing_strategy:
                    return {"status": "error", "message": f"Another strategy with name '{value}' already exists."}
            setattr(strategy, key, value)
            updated_count +=1
    
    if updated_count == 0:
        return {"status": "info", "message": "No valid fields provided for update."}

    try:
        db_session.commit()
        return {"status": "success", "message": "Strategy updated successfully."}
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error updating strategy {strategy_id}: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": f"Database error while updating strategy: {e}"}


# --- Admin Subscriptions & Payments Overview (Placeholders) ---
# These would require more detailed formatting and potentially complex queries
def list_all_subscriptions_admin(db_session: Session, page: int = 1, per_page: int = 20):
    """Lists all user strategy subscriptions with pagination."""
    try:
        query = db_session.query(UserStrategySubscription).join(User).join(Strategy)

        total_subscriptions = query.count()
        subscriptions_data = query.offset((page - 1) * per_page).limit(per_page).all()

        subscriptions_list = []
        for sub in subscriptions_data:
            subscriptions_list.append({
                "id": sub.id,
                "user_id": sub.user_id,
                "username": sub.user.username if sub.user else None,
                "strategy_id": sub.strategy_id,
                "strategy_name": sub.strategy.name if sub.strategy else None,
                "api_key_id": sub.api_key_id,
                # Assuming ApiKey model has a 'name' or identifier field
                # "api_key_name": sub.api_key.name if sub.api_key else None, # Uncomment if ApiKey has a name field
                "is_active": sub.is_active,
                "subscribed_at": sub.subscribed_at.isoformat() if sub.subscribed_at else None,
                "unsubscribed_at": sub.unsubscribed_at.isoformat() if sub.unsubscribed_at else None,
                "parameters": sub.parameters # Assuming parameters are stored as JSON or similar
            })

        return {
            "status": "success",
            "subscriptions": subscriptions_list,
            "total_subscriptions": total_subscriptions,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_subscriptions + per_page - 1) // per_page if per_page > 0 else 0
        }
    except Exception as e:
        logger.error(f"Admin: Error listing all subscriptions (page {page}): {e}", exc_info=True)
        return {"status": "error", "message": f"Could not retrieve subscriptions: {e}"}

def list_all_payments_admin(db_session: Session, page: int = 1, per_page: int = 20):
    """Lists all payment transactions with pagination."""
    try:
        query = db_session.query(PaymentTransaction).join(User)

        total_payments = query.count()
        payments_data = query.offset((page - 1) * per_page).limit(per_page).all()

        payments_list = []
        for payment in payments_data:
            payments_list.append({
                "id": payment.id,
                "user_id": payment.user_id,
                "username": payment.user.username if payment.user else None,
                "amount_usd": float(payment.amount_usd), # Ensure it's a standard number format
                "currency": payment.currency,
                "status": payment.status,
                "transaction_id": payment.transaction_id,
                "payment_method": payment.payment_method,
                "created_at": payment.created_at.isoformat() if payment.created_at else None,
                "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
                "metadata": payment.metadata # Assuming metadata is stored as JSON or similar
            })

        return {
            "status": "success",
            "payments": payments_list,
            "total_payments": total_payments,
            "page": page,
            "per_page": per_page,
            "total_pages": (total_payments + per_page - 1) // per_page if per_page > 0 else 0
        }
    except Exception as e:
        logger.error(f"Admin: Error listing all payments (page {page}): {e}", exc_info=True)
        return {"status": "error", "message": f"Could not retrieve payments: {e}"}

def get_total_revenue(db_session: Session):
    """Calculates the total revenue from completed payment transactions."""
    total_revenue = db_session.query(sqlalchemy.func.sum(PaymentTransaction.amount_usd)).filter(
        PaymentTransaction.status == "completed"
    ).scalar()
    return total_revenue if total_revenue is not None else 0.0

# --- Admin Site Settings Management (Conceptual) ---
def get_site_settings_admin(): # Removed db_session as it's reading env vars mostly
    # Sensitive settings should always come from env or secure config store
    settings_dict = {
       "PROJECT_NAME": settings.PROJECT_NAME,
       "PROJECT_VERSION": settings.PROJECT_VERSION,
       "DATABASE_URL_CONFIGURED": bool(settings.DATABASE_URL), # Don't expose the URL itself
       "JWT_SECRET_KEY_SET": settings.JWT_SECRET_KEY != "a_very_secure_default_secret_key_please_change_me", # Check if default
       "SMTP_HOST": settings.SMTP_HOST or "Not Set",
       "EMAILS_FROM_EMAIL": settings.EMAILS_FROM_EMAIL or "Not Set",
       "FRONTEND_URL": settings.FRONTEND_URL,
       "ALLOWED_ORIGINS": settings.ALLOWED_ORIGINS,
       "ENVIRONMENT": os.getenv("ENVIRONMENT", "Not Set")
       # Add other relevant non-sensitive settings or status of sensitive ones
    }
    return {"status": "success", "settings": settings_dict}

# Updating site settings via API is generally discouraged for anything sensitive.
# This function remains a placeholder and should be used with extreme caution.
def update_site_setting_admin(setting_key: str, setting_value: str):
    # This is highly conceptual. Modifying runtime config or .env via API is risky.
    # Best practice is to manage config via deployment process / env variables.
    if setting_key in ["DATABASE_URL", "JWT_SECRET_KEY", "SMTP_PASSWORD"]:
         return {"status": "error", "message": f"Setting '{setting_key}' is sensitive and cannot be updated via API."}
    
    logger.info(f"Admin: Simulated attempt to update site setting '{setting_key}' to '{setting_value}'. This is not implemented for security.") # Use logger instead of print
    return {"status": "info_simulated", "message": f"Updating setting '{setting_key}' is conceptual and not directly implemented for runtime changes."}
