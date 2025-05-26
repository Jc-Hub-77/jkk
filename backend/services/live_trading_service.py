# backend/services/live_trading_service.py
import datetime
import time
import ccxt
import json
import os
import importlib.util
import logging

from sqlalchemy.orm import Session

from backend.models import UserStrategySubscription, ApiKey, User, Strategy as StrategyModel
from backend.services.strategy_service import _load_strategy_class_from_db_obj
# from backend.services.exchange_service import _decrypt_data # _decrypt_data is not used directly here anymore
from backend.config import settings
from backend.celery_app import celery_app
from backend.tasks.live_trading_tasks import run_live_strategy_task # Import the NEW Celery task
from backend.services.exchange_service import get_encrypted_api_key_data # Import new method
from backend.db import SessionLocal # For db_session_factory in LiveStrategyRunner

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(processName)s - %(message)s')
logger = logging.getLogger(__name__)

# --- LiveStrategyRunner Class ---
class LiveStrategyRunner:
    def __init__(self, strategy, exchange, user_id: int, subscription_id: int, db_session_factory, task_id=None):
        self.strategy = strategy
        self.exchange = exchange
        self.user_id = user_id
        self.subscription_id = subscription_id
        self.db_session_factory = db_session_factory
        self.is_running = False
        self.task_id = task_id # Celery task ID

    def run(self):
        logger.info(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Starting strategy '{self.strategy.name}' for user {self.user_id} on {self.strategy.symbol}@{self.strategy.timeframe}.")
        self.is_running = True
        db_session = None # Initialize to None

        try:
            while self.is_running:
                # This loop represents the core trading logic execution cycle.
                # It needs to be gracefully stoppable by the self.is_running flag.
                # The Celery task itself will manage the is_running flag via self.request.is_terminated for graceful shutdown.

                # Create a new session for this cycle of operations
                db_session = self.db_session_factory()

                # Check subscription status from DB
                current_sub = db_session.query(UserStrategySubscription).filter(UserStrategySubscription.id == self.subscription_id).first()
                if not current_sub or not current_sub.is_active or \
                   (current_sub.expires_at and current_sub.expires_at <= datetime.datetime.utcnow()):
                    logger.info(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Subscription expired or deactivated. Stopping runner.")
                    if current_sub and current_sub.is_active: # If it was active but now determined to be stopped
                        current_sub.is_active = False
                        current_sub.status_message = "Stopped: Subscription expired or deactivated during run."
                        db_session.commit()
                    self.is_running = False # Signal loop to stop
                    break # Exit the while loop

                # Optional: Update status in DB to show "Running - Last check: ..."
                # current_sub.status_message = f"Running - Last check: {datetime.datetime.utcnow().isoformat()}"
                # db_session.commit()

                logger.debug(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Starting cycle for {self.strategy.symbol}@{self.strategy.timeframe}.")

                market_data = None
                try:
                    logger.debug(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Fetching OHLCV for {self.strategy.symbol}@{self.strategy.timeframe}.")
                    # Strategy should define its data needs, e.g., number of candles.
                    # For now, fetching last 100, adjust as needed.
                    ohlcv = self.exchange.fetch_ohlcv(self.strategy.symbol, self.strategy.timeframe, limit=100)
                    if ohlcv:
                        # The strategy instance might expect data in a specific format (e.g., pandas DataFrame)
                        # This transformation should happen before calling execute_live_signal or inside it.
                        market_data = ohlcv # Placeholder, actual transformation might be needed
                        logger.debug(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Fetched {len(ohlcv)} candles.")
                    else:
                        logger.warning(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] No OHLCV data fetched.")
                except ccxt.BaseError as e:
                    logger.error(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] CCXT error fetching market data: {e}", exc_info=True)
                    # Potentially update subscription status with data fetch error
                    current_sub.status_message = f"Data Fetch Error: {str(e)[:100]}"
                    db_session.commit()
                except Exception as e:
                    logger.error(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Unexpected error fetching market data: {e}", exc_info=True)
                    current_sub.status_message = f"Data Fetch Error (Other): {str(e)[:100]}"
                    db_session.commit()


                if market_data:
                    try:
                        logger.debug(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Calling execute_live_signal for '{self.strategy.name}'.")
                        # The strategy's execute_live_signal method handles its logic, including placing orders.
                        # It needs the db_session for recording trades/orders.
                        self.strategy.execute_live_signal(market_data, self.exchange, db_session, self.subscription_id)
                        current_sub.status_message = f"Running - Last execution: {datetime.datetime.utcnow().isoformat()}"
                        db_session.commit()
                        logger.debug(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Strategy execute_live_signal completed.")
                    except Exception as e:
                        logger.error(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Error in strategy execute_live_signal: {e}", exc_info=True)
                        current_sub.status_message = f"Execution Error: {str(e)[:100]}"
                        db_session.commit()
                else:
                    logger.warning(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Skipping strategy execution due to no market data.")

                sleep_duration_seconds = self._calculate_sleep_duration()
                logger.debug(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Cycle complete. Sleeping for {sleep_duration_seconds}s.")
                
                # Graceful sleep: check is_running periodically during sleep
                for _ in range(int(sleep_duration_seconds)):
                    if not self.is_running: # Check if stop has been signaled
                        break
                    time.sleep(1)
                if not self.is_running: # If stop was signaled during sleep, exit outer loop
                    break
            
        except Exception as e:
            logger.error(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Critical error in LiveStrategyRunner: {e}", exc_info=True)
            if db_session: # Ensure db_session is available
                sub_to_update = db_session.query(UserStrategySubscription).filter(UserStrategySubscription.id == self.subscription_id).first()
                if sub_to_update:
                    sub_to_update.status_message = f"Critical Runner Error: {str(e)[:100]}"
                    sub_to_update.is_active = False
                    db_session.commit()
        finally:
            if db_session: # Ensure db_session is closed if it was opened
                db_session.close()
            logger.info(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] LiveStrategyRunner for strategy '{self.strategy.name}' stopped.")

    def stop(self): # This method can be called by the Celery task on revoke
        logger.info(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Received stop signal for strategy '{self.strategy.name}'.")
        self.is_running = False

    def _calculate_sleep_duration(self):
        timeframe = self.strategy.timeframe
        sleep_duration_seconds = 60 # Default
        try:
            value = int(timeframe[:-1])
            unit = timeframe[-1]
            if unit == 'm':
                sleep_duration_seconds = max(1, value * 60 - 15) # Offset to ensure new candle data
            elif unit == 'h':
                sleep_duration_seconds = max(60, value * 3600 - 300) # Offset
            elif unit == 'd':
                sleep_duration_seconds = max(3600, value * 86400 - 3600) # Offset
            return sleep_duration_seconds
        except ValueError:
            logger.warning(f"[Runner SubID {self.subscription_id}, TaskID {self.task_id}] Could not parse timeframe '{timeframe}' for sleep. Using default 60s.")
            return 60

# --- Service Functions ---
def deploy_strategy(db: Session, user_strategy_subscription_id: int):
    user_sub = db.query(UserStrategySubscription).filter(
        UserStrategySubscription.id == user_strategy_subscription_id
    ).first()

    if not user_sub:
        logger.error(f"Subscription ID {user_strategy_subscription_id} not found for deployment.")
        return {"status": "error", "message": "Subscription not found."}
    if not user_sub.is_active:
         logger.warning(f"Subscription ID {user_strategy_subscription_id} is not active. Cannot deploy.")
         return {"status": "error", "message": "Subscription is not active."}
    if user_sub.expires_at and user_sub.expires_at <= datetime.datetime.utcnow():
        user_sub.is_active = False
        user_sub.status_message = "Stopped: Subscription expired before deployment attempt."
        db.commit()
        logger.warning(f"Subscription ID {user_strategy_subscription_id} has expired. Cannot deploy.")
        return {"status": "error", "message": "Subscription has expired."}

    strategy_db_obj = db.query(StrategyModel).filter(StrategyModel.id == user_sub.strategy_id).first()
    if not strategy_db_obj:
        logger.error(f"Strategy DB object ID {user_sub.strategy_id} not found for subscription {user_strategy_subscription_id}.")
        user_sub.status_message = "Error: Strategy definition not found."
        user_sub.is_active = False
        db.commit()
        return {"status": "error", "message": "Strategy definition not found."}

    encrypted_api_key_data = get_encrypted_api_key_data(db_session=db, api_key_id=user_sub.api_key_id, user_id=user_sub.user_id)
    if not encrypted_api_key_data:
        logger.error(f"Failed to retrieve encrypted API key data for API Key ID {user_sub.api_key_id}, User ID {user_sub.user_id}.")
        user_sub.status_message = "Error: Failed to get API key data for deployment."
        user_sub.is_active = False
        db.commit()
        return {"status": "error", "message": "Failed to retrieve API key data."}
    
    try:
        custom_params = json.loads(user_sub.custom_parameters) if isinstance(user_sub.custom_parameters, str) else user_sub.custom_parameters
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in custom_parameters for subscription {user_strategy_subscription_id}: {user_sub.custom_parameters}")
        user_sub.status_message = "Error: Invalid strategy parameters (JSON)."
        user_sub.is_active = False
        db.commit()
        return {"status": "error", "message": "Invalid strategy parameters."}

    task_params = {
        "user_id": user_sub.user_id,
        "subscription_id": user_sub.id,
        "strategy_name": strategy_db_obj.class_name,
        "strategy_file_path": strategy_db_obj.file_path,
        "symbol": custom_params.get("symbol", "BTC/USDT"), # Default if not in params
        "timeframe": custom_params.get("timeframe", "1h"), # Default if not in params
        "settings_json": user_sub.custom_parameters,
        "encrypted_api_key_data": encrypted_api_key_data,
        # Pass SessionLocal (or a compatible factory) for the task to create DB sessions
        "db_session_factory_pickleable": "backend.db.SessionLocal" # Path to the factory
    }

    try:
        task = run_live_strategy_task.delay(**task_params)
        user_sub.celery_task_id = task.id
        user_sub.status_message = f"Queued - Task ID: {task.id}"
        db.commit()
        logger.info(f"Queued strategy deployment task for subscription ID: {user_strategy_subscription_id} with Task ID: {task.id}. Params: {task_params}")
        return {"status": "success", "message": f"Strategy deployment task queued. Task ID: {task.id}"}

    except Exception as e:
         logger.error(f"Failed to send Celery task for subscription {user_strategy_subscription_id}: {e}", exc_info=True)
         user_sub.status_message = f"Deployment Error: Failed to queue task - {str(e)[:100]}"
         db.commit()
         return {"status": "error", "message": f"Internal server error during deployment: {e}"}


def stop_strategy(db: Session, user_strategy_subscription_id: int):
    user_sub = db.query(UserStrategySubscription).filter(
        UserStrategySubscription.id == user_strategy_subscription_id
    ).first()

    if not user_sub:
        return {"status": "error", "message": "Subscription not found."}

    celery_task_id = user_sub.celery_task_id
    if celery_task_id:
        try:
            celery_app.control.revoke(celery_task_id, terminate=True, signal='SIGTERM') # Send SIGTERM
            logger.info(f"Sent revoke signal (SIGTERM) for Celery task ID: {celery_task_id} (Subscription ID: {user_strategy_subscription_id})")
            message = f"Stop signal sent to strategy task {celery_task_id}."

            user_sub.is_active = False
            user_sub.status_message = f"Stop signal sent at {datetime.datetime.utcnow().isoformat()}"
            # user_sub.celery_task_id = None # Clearing task ID here might be premature, task needs to confirm stop.
            db.commit()
            return {"status": "success", "message": message}
        except Exception as e:
            logger.error(f"Failed to send revoke signal for task {celery_task_id}: {e}", exc_info=True)
            user_sub.status_message = f"Stop Error: Failed to revoke task - {str(e)[:100]}"
            db.commit()
            return {"status": "error", "message": f"Failed to stop strategy task: {e}"}
    else:
        user_sub.is_active = False
        user_sub.status_message = f"Stopped (Task ID not found) at {datetime.datetime.utcnow().isoformat()}"
        db.commit()
        logger.warning(f"No Celery task ID found for subscription {user_strategy_subscription_id}. Updated DB status only.")
        return {"status": "info", "message": "No running task found for this subscription. Status updated in DB."}


def get_running_strategies_status():
    logger.info("Fetching running strategy statuses from Celery backend (placeholder)...")
    # Actual implementation would involve querying Celery, e.g., using Flower API or celery inspect.
    # For now, this remains a placeholder.
    return {"status": "info", "running_strategies": [{"message": "Status retrieval from Celery backend is a TODO."}]}


def auto_stop_expired_subscriptions(db: Session):
    logger.info("Checking for expired subscriptions to stop...")
    expired_subs = db.query(UserStrategySubscription).filter(
        UserStrategySubscription.is_active == True,
        UserStrategySubscription.expires_at <= datetime.datetime.utcnow()
    ).all()

    for sub in expired_subs:
        logger.info(f"Subscription ID {sub.id} for user {sub.user_id} has expired. Attempting to stop Celery task.")
        stop_response = stop_strategy(db, sub.id) # This will now use revoke with SIGTERM
        logger.info(f"Stop response for sub {sub.id}: {stop_response}")

    if expired_subs:
        logger.info(f"Processed {len(expired_subs)} expired subscriptions for stopping.")

# Note: The LiveStrategyRunner is now defined in this file.
# The Celery task run_live_strategy_task in live_trading_tasks.py will need to import it.
# Ensure API_ENCRYPTION_KEY is available to Celery workers via settings.
# The db_session_factory_pickleable parameter is a string path to SessionLocal,
# the Celery task will import and use it to create new DB sessions.
# This avoids passing non-pickleable objects like DB sessions directly to tasks.
