# backend/schemas/admin_schemas.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Any
import datetime
from .user_schemas import UserBase # Re-use UserBase or define specific admin views

# --- Admin User Management Schemas ---
class AdminUserView(UserBase): # What an admin sees for a user
    id: int
    is_admin: bool
    email_verified: bool
    # is_active: bool # If User model gets an is_active field
    created_at: datetime.datetime
    profile_full_name: Optional[str] = None

    class Config:
        orm_mode = True

class AdminUserListResponse(BaseModel):
    status: str
    users: List[AdminUserView]
    total_users: int
    page: int
    per_page: int
    total_pages: int

class AdminSetAdminStatusRequest(BaseModel):
    user_id: int
    make_admin: bool

# class AdminToggleUserActiveRequest(BaseModel): # If is_active is implemented
#     user_id: int
#     activate: bool


# --- Admin Strategy Management Schemas ---
class AdminStrategyView(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    python_code_path: str
    default_parameters: Optional[str] = None # JSON string
    category: Optional[str] = None
    risk_level: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime.datetime] = None

    class Config:
        orm_mode = True

class AdminStrategyListResponse(BaseModel):
    status: str
    strategies: List[AdminStrategyView]

class AdminStrategyCreateRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
    python_code_path: str = Field(..., min_length=5) # e.g., strategies/my_strategy.py
    default_parameters: Optional[str] = "{}" # JSON string
    category: Optional[str] = Field(None, max_length=50)
    risk_level: Optional[str] = Field(None, max_length=20) # e.g., Low, Medium, High

class AdminStrategyUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
    python_code_path: Optional[str] = Field(None, min_length=5)
    default_parameters: Optional[str] = None # JSON string
    category: Optional[str] = Field(None, max_length=50)
    risk_level: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None


# --- Admin Site Settings Schemas ---
class AdminSiteSettingsResponse(BaseModel):
    status: str
    settings: dict[str, Any]

# General response for admin actions
class AdminActionResponse(BaseModel):
    status: str
    message: str
    detail: Optional[Any] = None


# --- Admin Subscription Management Schemas ---
class AdminSubscriptionItem(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    strategy_id: int
    strategy_name: Optional[str] = None
    api_key_id: int
    api_key_label: Optional[str] = None
    is_active: bool
    subscribed_at: Optional[datetime.datetime] = None
    expires_at: Optional[datetime.datetime] = None
    custom_parameters: Optional[Any] = None # Can be dict after parsing, or str
    status_message: Optional[str] = None
    celery_task_id: Optional[str] = None # Added this field based on model

    class Config:
        orm_mode = True

class AdminSubscriptionListResponse(BaseModel):
    status: str
    subscriptions: List[AdminSubscriptionItem]
    total_subscriptions: int
    page: int
    per_page: int
    total_pages: int

class AdminSubscriptionUpdateRequest(BaseModel):
    new_status_message: Optional[str] = Field(None, max_length=255)
    new_is_active: Optional[bool] = None
    new_expires_at_str: Optional[str] = Field(None, description="ISO format datetime string, e.g., YYYY-MM-DDTHH:MM:SS")


# --- Admin Dashboard Schemas ---
class AdminDashboardSummaryData(BaseModel):
    totalUsers: int
    totalRevenue: float
    activeSubscriptions: int
    totalStrategies: int

class AdminDashboardSummaryResponse(BaseModel):
    status: str
    summary: AdminDashboardSummaryData
