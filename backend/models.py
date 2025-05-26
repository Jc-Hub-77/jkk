# backend/models.py
import datetime
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import sessionmaker, relationship, declarative_base 

engine = None 
SessionLocal = None 
Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    email_verified = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    referral_code = Column(String, unique=True, index=True, nullable=True)
    referred_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    email_verification_token = Column(String, unique=True, nullable=True)
    email_verification_token_expires_at = Column(DateTime, nullable=True)
    password_reset_token = Column(String, unique=True, nullable=True)
    password_reset_token_expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)

    profile = relationship("Profile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    strategy_subscriptions = relationship("UserStrategySubscription", back_populates="user", cascade="all, delete-orphan")
    backtest_results = relationship("BacktestResult", back_populates="user", cascade="all, delete-orphan")
    payment_transactions = relationship("PaymentTransaction", back_populates="user", cascade="all, delete-orphan")
    referrals_made = relationship("Referral", foreign_keys="Referral.referrer_user_id", back_populates="referrer", cascade="all, delete-orphan")
    backtest_reports = relationship("BacktestReport", back_populates="user", cascade="all, delete-orphan")
    
    # Relationship to Orders (one user can have many orders)
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    # Relationship to Positions (one user can have many positions)
    positions = relationship("Position", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', email='{self.email}')>"

class Profile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    full_name = Column(String, index=True, nullable=True)
    bio = Column(Text, nullable=True)
    user = relationship("User", back_populates="profile")
    def __repr__(self):
        return f"<Profile(user_id={self.user_id}, full_name='{self.full_name}')>"

class ApiKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    exchange_name = Column(String, nullable=False, index=True)
    label = Column(String, nullable=True)
    api_key_public_preview = Column(String, nullable=True)
    encrypted_api_key = Column(Text, nullable=False)
    encrypted_secret_key = Column(Text, nullable=False)
    encrypted_passphrase = Column(Text, nullable=True)
    status = Column(String, default="pending_verification", index=True)
    status_message = Column(Text, nullable=True)
    last_tested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    user = relationship("User", back_populates="api_keys")
    def __repr__(self):
        return f"<ApiKey(id={self.id}, user_id='{self.user_id}', exchange='{self.exchange_name}', label='{self.label}')>"

class Strategy(Base):
    __tablename__ = "strategies"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    class_name = Column(String, nullable=False) 
    file_path = Column(String, nullable=False) 
    description = Column(Text, nullable=True)
    default_parameters = Column(Text) 
    category = Column(String) 
    risk_level = Column(String) 
    historical_performance_summary = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True) 
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    subscriptions = relationship("UserStrategySubscription", back_populates="strategy")
    def __repr__(self):
        return f"<Strategy(id={self.id}, name='{self.name}', class_name='{self.class_name}')>"

class UserStrategySubscription(Base):
    __tablename__ = "user_strategy_subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=False) 
    custom_parameters = Column(Text) 
    is_active = Column(Boolean, default=False) 
    subscribed_at = Column(DateTime, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime, nullable=True) 
    backtest_results_id = Column(Integer, ForeignKey("backtest_results.id"), nullable=True) 
    status_message = Column(String, nullable=True) 
    celery_task_id = Column(String, nullable=True, index=True) 
    user = relationship("User", back_populates="strategy_subscriptions")
    strategy = relationship("Strategy", back_populates="subscriptions")
    api_key = relationship("ApiKey") 
    orders = relationship("Order", back_populates="subscription", cascade="all, delete-orphan")
    # A subscription might have one current position per symbol, or one overall position.
    # If one overall position per subscription (for its designated symbol):
    position = relationship("Position", back_populates="subscription", uselist=False, cascade="all, delete-orphan") # Changed to one-to-one
    def __repr__(self):
        return f"<UserStrategySubscription(id={self.id}, user_id={self.user_id}, strategy_id={self.strategy_id}, active={self.is_active})>"

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey("user_strategy_subscriptions.id"), nullable=False, index=True)
    celery_task_id = Column(String, nullable=True, index=True, comment="Celery task ID of the strategy runner")
    exchange_order_id = Column(String, nullable=True, index=True, comment="Order ID from the exchange")
    client_order_id = Column(String, nullable=True, index=True, comment="Client-generated order ID, if used")
    strategy_name = Column(String, nullable=False) # Name of the strategy that generated this order
    symbol = Column(String, nullable=False, index=True)
    order_type = Column(String, nullable=False, comment="e.g., MARKET, LIMIT, STOP_LOSS, TAKE_PROFIT")
    side = Column(String, nullable=False, comment="e.g., BUY, SELL")
    price = Column(Float, nullable=True, comment="Limit price for limit orders")
    stop_price = Column(Float, nullable=True, comment="Stop price for stop orders")
    quantity = Column(Float, nullable=False) # Renamed from 'amount' for clarity
    filled_quantity = Column(Float, nullable=True, default=0.0) # Renamed from 'filled'
    average_fill_price = Column(Float, nullable=True)
    status = Column(String, nullable=False, index=True, comment="e.g., NEW, OPEN, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED")
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, comment="Timestamp of order creation by our system") # System timestamp
    exchange_timestamp = Column(DateTime, nullable=True, comment="Timestamp from the exchange, if available") # Exchange timestamp
    # cost = Column(Float, nullable=True) # Can be calculated: filled_quantity * average_fill_price
    # remaining = Column(Float, nullable=True) # Can be calculated: quantity - filled_quantity
    # closed_at = Column(DateTime, nullable=True) # Can be inferred from status and updated_at
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    user = relationship("User", back_populates="orders")
    subscription = relationship("UserStrategySubscription", back_populates="orders")
    def __repr__(self):
        return f"<Order(id={self.id}, user_id={self.user_id}, symbol='{self.symbol}', side='{self.side}', status='{self.status}')>"

class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Made subscription_id unique as per requirement, implying one position per subscription.
    # If a subscription can manage positions for multiple symbols, this needs to be a composite key or handled differently.
    subscription_id = Column(Integer, ForeignKey("user_strategy_subscriptions.id"), nullable=False, index=True, unique=True, comment="Ensures one position record per subscription (for its primary symbol)")
    celery_task_id = Column(String, nullable=True, index=True, comment="Celery task ID of the strategy runner")
    strategy_name = Column(String, nullable=False) # Name of the strategy managing this position
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False, comment="e.g., LONG, SHORT") # 'long' or 'short'
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False) # Renamed from 'amount'
    current_market_price = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, nullable=True)
    realized_pnl = Column(Float, nullable=True, default=0.0)
    leverage = Column(Float, nullable=True, default=1.0)
    liquidation_price = Column(Float, nullable=True)
    margin = Column(Float, nullable=True) # Margin used for the position
    # is_open = Column(Boolean, default=True, index=True) # Can be inferred: true if closed_at is null.
    # exchange_name = Column(String, nullable=False) # Can be derived from subscription's API key
    last_updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow) # Renamed from updated_at
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # closed_at = Column(DateTime, nullable=True) # To explicitly mark when a position is fully closed out.

    user = relationship("User", back_populates="positions")
    subscription = relationship("UserStrategySubscription", back_populates="position") # Changed to one-to-one
    def __repr__(self):
        return f"<Position(id={self.id}, user_id={self.user_id}, symbol='{self.symbol}', side='{self.side}')>"

class BacktestResult(Base):
    __tablename__ = "backtest_results"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    strategy_name_used = Column(String, index=True, nullable=False)
    strategy_code_snapshot = Column(Text, nullable=True) 
    custom_parameters_json = Column(Text) 
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    timeframe = Column(String, nullable=False)
    symbol = Column(String, index=True, nullable=False)
    pnl = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    total_trades = Column(Integer, nullable=True)
    winning_trades = Column(Integer, nullable=True)
    losing_trades = Column(Integer, nullable=True)
    trades_log_json = Column(Text) 
    equity_curve_json = Column(Text) 
    status = Column(String, default="queued", index=True) 
    celery_task_id = Column(String, nullable=True, index=True) 
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow) 
    user = relationship("User", back_populates="backtest_results")
    def __repr__(self):
        return f"<BacktestResult(id={self.id}, strategy_name_used='{self.strategy_name_used}', user_id={self.user_id}, status='{self.status}', pnl={self.pnl})>"

class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_strategy_subscription_id = Column(Integer, ForeignKey("user_strategy_subscriptions.id"), nullable=True) 
    amount_crypto = Column(Float, nullable=False)
    crypto_currency = Column(String, nullable=False)
    usd_equivalent = Column(Float, nullable=True) 
    payment_gateway = Column(String) 
    gateway_transaction_id = Column(String, unique=True, index=True, nullable=True) 
    internal_reference = Column(String, unique=True, index=True, nullable=True) 
    status = Column(String, default="pending", index=True) 
    description = Column(Text, nullable=True) 
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    user = relationship("User", back_populates="payment_transactions")
    def __repr__(self):
        return f"<PaymentTransaction(id={self.id}, user_id={self.user_id}, status='{self.status}')>"

class Referral(Base):
    __tablename__ = "referrals"
    id = Column(Integer, primary_key=True, index=True)
    referrer_user_id = Column(Integer, ForeignKey("users.id"), nullable=False) 
    referred_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True) 
    signed_up_at = Column(DateTime, default=datetime.datetime.utcnow)
    first_payment_at = Column(DateTime, nullable=True) 
    commission_earned_total = Column(Float, default=0.0) 
    commission_pending_payout = Column(Float, default=0.0) 
    commission_paid_out_total = Column(Float, default=0.0) 
    last_payout_date = Column(DateTime, nullable=True)
    is_active_for_commission = Column(Boolean, default=True) 
    referrer = relationship("User", foreign_keys=[referrer_user_id], back_populates="referrals_made")
    def __repr__(self):
        return f"<Referral(id={self.id}, referrer_id={self.referrer_user_id}, referred_id={self.referred_user_id})>"

class BacktestReport(Base): 
    __tablename__ = "backtest_reports"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    celery_task_id = Column(String, nullable=True, index=True) 
    strategy_name = Column(String, index=True, nullable=False) 
    strategy_file_path = Column(String, nullable=True) 
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    custom_settings_json = Column(Text, nullable=True) 
    status = Column(String, default="PENDING", index=True) 
    results_json = Column(Text, nullable=True) 
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    user = relationship("User", back_populates="backtest_reports")
    def __repr__(self):
        return f"<BacktestReport(id={self.id}, user_id={self.user_id}, strategy='{self.strategy_name}', status='{self.status}')>"

def init_db(database_url: str):
    global engine, SessionLocal
    engine = create_engine(database_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine

print("models.py loaded. Call init_db(DATABASE_URL) from your main application to set up the database engine.")
