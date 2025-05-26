# backend/api/v1/admin_router.py
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from ...schemas import admin_schemas, user_schemas # General response might be in user_schemas
from backend.services import admin_service, user_service
from backend.dependencies import get_current_active_admin_user
from backend.models import User
from backend.db import get_db

router = APIRouter()

# --- Admin User Management Endpoints ---
@router.get("/users", response_model=admin_schemas.AdminUserListResponse, dependencies=[Depends(get_current_active_admin_user)])
async def admin_list_users(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100), # Max 100 per page
    search_term: Optional[str] = Query(None),
    sort_by: str = Query("id", enum=["id", "username", "email", "created_at"]), # Allowed sort fields
    sort_order: str = Query("asc", enum=["asc", "desc"])
):
    result = admin_service.list_all_users(db, page, per_page, search_term, sort_by, sort_order)
    if result["status"] == "error": # Should not happen if service layer is robust
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result.get("message", "Error listing users"))
    return result

@router.post("/users/set-admin-status", response_model=user_schemas.GeneralResponse, dependencies=[Depends(get_current_active_admin_user)])
async def admin_set_user_admin_status(
    request_data: admin_schemas.AdminSetAdminStatusRequest,
    db: Session = Depends(get_db)
):
    result = admin_service.set_user_admin_status(db, request_data.user_id, request_data.make_admin)
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.post("/users/toggle-email-verified", response_model=user_schemas.GeneralResponse, dependencies=[Depends(get_current_active_admin_user)])
async def admin_toggle_user_email_verified(
    request_data: admin_schemas.AdminSetAdminStatusRequest,
    db: Session = Depends(get_db)
):
    result = admin_service.toggle_user_email_verified(db, request_data.user_id)
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.post("/users/toggle-active-status", response_model=user_schemas.GeneralResponse, dependencies=[Depends(get_current_active_admin_user)])
async def admin_toggle_user_active_status(
    request_data: admin_schemas.AdminSetAdminStatusRequest, # Re-use schema, 'make_admin' field will be used for 'is_active'
    db: Session = Depends(get_db)
):
    """
    Admin endpoint to toggle the active status of a user.
    Uses AdminSetAdminStatusRequest schema, interpreting 'make_admin' as 'is_active'.
    """
    result = user_service.toggle_user_active_status(db, request_data.user_id, request_data.make_admin) # Pass make_admin as is_active
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result


# --- Admin Strategy Management Endpoints ---
@router.get("/strategies", response_model=admin_schemas.AdminStrategyListResponse, dependencies=[Depends(get_db)])
async def admin_list_strategies(db: Session = Depends(get_db)):
    result = admin_service.list_all_strategies_admin(db)
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result.get("message", "Error listing strategies"))
    return result

@router.post("/strategies", response_model=admin_schemas.AdminActionResponse, status_code=status.HTTP_201_CREATED)
async def admin_add_strategy(
    strategy_data: admin_schemas.AdminStrategyCreateRequest,
    db: Session = Depends(get_db)
):
    result = admin_service.add_new_strategy_admin(
        db_session=db,
        name=strategy_data.name,
        description=strategy_data.description,
        python_code_path=strategy_data.python_code_path,
        default_parameters=strategy_data.default_parameters,
        category=strategy_data.category,
        risk_level=strategy_data.risk_level
    )
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    return result

@router.put("/strategies/{strategy_id}", response_model=admin_schemas.AdminActionResponse)
async def admin_update_strategy(
    strategy_id: int,
    strategy_update_data: admin_schemas.AdminStrategyUpdateRequest,
    db: Session = Depends(get_db)
):
    updates = strategy_update_data.dict(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No update data provided.")

    result = admin_service.update_strategy_admin(db, strategy_id, updates)
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    elif result["status"] == "info": # e.g. no valid fields provided
        return JSONResponse(status_code=status.HTTP_200_OK, content=result) # Or 304 Not Modified
    return result


# --- Admin Site Settings Endpoint ---
@router.get("/site-settings", response_model=admin_schemas.AdminSiteSettingsResponse)
async def admin_get_site_settings():
    # This service function doesn't need db session as it reads from config/env
    result = admin_service.get_site_settings_admin()
    return result

# Note: Updating site settings via API is generally complex and risky, especially for env vars.
# The service layer function is a placeholder. Exposing it via API needs careful consideration.
# For now, I'll omit the PUT endpoint for site settings.

# --- Admin Dashboard Data ---
@router.get("/dashboard-summary", dependencies=[Depends(get_db)])
async def admin_dashboard_summary(db: Session = Depends(get_db)):
    """
    Retrieves summary data for the admin dashboard.
    """
    total_users_result = admin_service.list_all_users(db, page=1, per_page=1) # Get total count from list_all_users
    total_users = total_users_result.get("total_users", 0)

    total_revenue = admin_service.get_total_revenue(db)

    # TODO: Add more summary stats (e.g., active subscriptions, total strategies)

    return {
        "status": "success",
        "summary": {
            "totalUsers": total_users,
            "totalRevenueLast30d": total_revenue, # Assuming total revenue is sufficient for now
            # Add other stats here as implemented
            "activeSubscriptions": 0, # Placeholder
            "totalStrategies": 0 # Placeholder
        }
    }


# --- Public Strategy Endpoints (Accessible without Admin) ---
@router.get("/strategies", response_model=admin_schemas.AdminStrategyListResponse) # Using AdminStrategyListResponse schema for now, might need a public schema
async def list_available_strategies(db: Session = Depends(get_db)):
    """
    Lists all available trading strategies.
    """
    # For now, return hardcoded strategies similar to frontend simulation
    # In a real app, this would fetch from DB or a strategy registry
    strategies = [
        {"id": "ema_crossover_v1", "name": "EMA Crossover", "description": "A simple Exponential Moving Average crossover strategy.", "category": "Trend Following", "risk_level": "Medium", "historical_performance_summary": "Good in trends."},
        {"id": "rsi_divergence_v1", "name": "RSI Divergence", "description": "Trades on bullish and bearish divergences.", "category": "Oscillator", "risk_level": "Medium-High", "historical_performance_summary": "Good for reversals."}
    ]
    return strategies

@router.get("/strategies/{strategy_id}", response_model=admin_schemas.AdminStrategyView) # Using a conceptual schema
async def get_strategy_details(strategy_id: str, db: Session = Depends(get_db)):
    """
    Gets details and parameters definition for a specific strategy.
    """
    # For now, return hardcoded data based on strategy_id
    # In a real app, this would fetch from DB or a strategy registry
    strategy_details = {
        "ema_crossover_v1": {
            "id": "ema_crossover_v1",
            "name": "EMA Crossover",
            "description": "A simple Exponential Moving Average crossover strategy.",
            "category": "Trend Following",
            "risk_level": "Medium",
            "historical_performance_summary": "Good in trends.",
            "parameters_definition": {
                "short_ema_period": {"type": "int", "default": 10, "min": 5, "max": 50, "label": "Short EMA Period"},
                "long_ema_period": {"type": "int", "default": 20, "min": 10, "max": 100, "label": "Long EMA Period"},
                "capital": {"type": "float", "default": 10000, "min": 100, "label": "Initial Capital (USD)"},
                "risk_per_trade": {"type": "float", "default": 0.01, "min": 0.001, "max": 0.1, "label": "Risk per Trade (%)", "step": "0.001"}
            }
        },
        "rsi_divergence_v1": {
            "id": "rsi_divergence_v1",
            "name": "RSI Divergence",
            "description": "Trades on bullish and bearish divergences.",
            "category": "Oscillator",
            "risk_level": "Medium-High",
            "historical_performance_summary": "Good for reversals.",
            "parameters_definition": {
                "rsi_period": {"type": "int", "default": 14, "min": 7, "max": 30, "label": "RSI Period"},
                "lookback_period": {"type": "int", "default": 20, "min": 10, "max": 50, "label": "Divergence Lookback"},
                "capital": {"type": "float", "default": 10000, "min": 100, "label": "Initial Capital (USD)"},
                "risk_per_trade": {"type": "float", "default": 0.015, "min": 0.001, "max": 0.1, "label": "Risk per Trade (%)", "step": "0.001"}
            }
        }
    }
    
    details = strategy_details.get(strategy_id)
    if not details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
        
    return {"status": "success", "details": details} # Wrap in "details" to match frontend expectation


from fastapi.responses import JSONResponse # For the info case in update_strategy
