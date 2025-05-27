import datetime
import time
import ccxt
import json
import os
import importlib.util
import logging

from sqlalchemy.orm import Session
from backend.celery_app import celery_app
from backend.models import UserStrategySubscription, ApiKey, User, Strategy as StrategyModel
from backend.services.strategy_service import _load_strategy_class_from_db_obj
from backend.services.exchange_service import _decrypt_data
from backend.services.backtesting_service import _perform_backtest_logic # Import backtesting logic
from backend.config import settings # For logging or other configs if needed
from backend.db import SessionLocal # Assuming SessionLocal is defined here or can be imported

# --- Logging Setup ---
logger = logging.getLogger(__name__)
# Configure logging for tasks if needed, or rely on Celery worker logging setup

# --- Celery Task Definitions ---

@celery_app.task(bind=True)
def run_live_strategy(self, user_sub_id: int):
    """
    Celery task to run a live trading strategy for a specific subscription.
    """
    db_session = None
    try:
        db_session = SessionLocal() # Get a new DB session for this task

        # --- Initialize Components (Similar to LiveStrategyRunner._initialize_components) ---
        user_sub = db_session.query(UserStrategySubscription).filter(UserStrategySubscription.id == user_sub_id).first()
        if not user_sub or not user_sub.is_active or (user_sub.expires_at and user_sub.expires_at <= datetime.datetime.utcnow()):
            logger.info(f"[SubID {user_sub_id}] Subscription not found, inactive, or expired. Stopping task.")
            if user_sub and user_sub.is_active: # If expired but still marked active
                user_sub.is_active = False
                user_sub.status_message = "Stopped: Subscription expired or deactivated."
                db_session.commit()
            return {"status": "stopped", "message": "Subscription inactive or expired."}

        strategy_db_obj = db_session.query(StrategyModel).filter(StrategyModel.id == user_sub.strategy_id).first()
        if not strategy_db_obj:
            logger.error(f"[SubID {user_sub_id}] Strategy DB object ID {user_sub.strategy_id} not found. Stopping task.")
            user_sub.status_message = "Error: Strategy not found."
            user_sub.is_active = False
            db_session.commit()
            return {"status": "error", "message": "Strategy not found."}

        StrategyClass = _load_strategy_class_from_db_obj(strategy_db_obj)
        if not StrategyClass:
            logger.error(f"[SubID {user_sub_id}] Could not load strategy class for {strategy_db_obj.name}. Stopping task.")
            user_sub.status_message = "Error: Could not load strategy class."
            user_sub.is_active = False
            db_session.commit()
            return {"status": "error", "message": "Could not load strategy class."}

        try:
            custom_params = json.loads(user_sub.custom_parameters) if isinstance(user_sub.custom_parameters, str) else user_sub.custom_parameters
        except json.JSONDecodeError:
            logger.error(f"[SubID {user_sub_id}] Invalid JSON in custom_parameters: {user_sub.custom_parameters}. Stopping task.")
            user_sub.status_message = "Error: Invalid strategy parameters."
            user_sub.is_active = False
            db_session.commit()
            return {"status": "error", "message": "Invalid strategy parameters."}

        symbol = custom_params.get("symbol", "BTC/USDT")
        timeframe = custom_params.get("timeframe", "1h")

        init_params = {
            "symbol": symbol, "timeframe": timeframe,
            "capital": custom_params.get("capital", 10000),
            **custom_params
        }
        try:
            strategy_instance = StrategyClass(**init_params)
            strategy_instance.name = strategy_db_obj.name
        except Exception as e:
            logger.error(f"[SubID {user_sub_id}] Error initializing strategy class '{strategy_db_obj.name}': {e}", exc_info=True)
            user_sub.status_message = f"Error initializing strategy: {str(e)[:100]}"
            user_sub.is_active = False
            db_session.commit()
            return {"status": "error", "message": f"Error initializing strategy: {e}"}

        api_key_record = db_session.query(ApiKey).filter(ApiKey.id == user_sub.api_key_id, ApiKey.user_id == user_sub.user_id).first()
        if not api_key_record or api_key_record.status != "active":
            logger.error(f"[SubID {user_sub_id}] API Key ID {user_sub.api_key_id} not found or not active for user {user_sub.user_id}. Stopping task.")
            user_sub.status_message = "Error: API Key not found or inactive."
            user_sub.is_active = False
            db_session.commit()
            return {"status": "error", "message": "API Key not found or inactive."}

        try:
            decrypted_key_public = _decrypt_data(api_key_record.encrypted_api_key)
            decrypted_secret = _decrypt_data(api_key_record.encrypted_secret_key)
            decrypted_passphrase = _decrypt_data(api_key_record.encrypted_passphrase) if api_key_record.encrypted_passphrase else None
        except ValueError as e:
            logger.error(f"[SubID {user_sub_id}] Failed to decrypt API credentials for key ID {user_sub.api_key_id}: {e}. Stopping task.")
            user_sub.status_message = "Error: Failed to decrypt API credentials."
            user_sub.is_active = False
            db_session.commit()
            return {"status": "error", "message": "Failed to decrypt API credentials."}

        exchange_id = api_key_record.exchange_name.lower()
        if not hasattr(ccxt, exchange_id):
            logger.error(f"[SubID {user_sub_id}] Exchange {exchange_id} not supported by CCXT. Stopping task.")
            user_sub.status_message = "Error: Exchange not supported."
            user_sub.is_active = False
            db_session.commit()
            return {"status": "error", "message": "Exchange not supported."}

        exchange_class = getattr(ccxt, exchange_id)
        config = {
            'apiKey': decrypted_key_public,
            'secret': decrypted_secret,
            'options': {'adjustForTimeDifference': True}, 'enableRateLimit': True,
        }
        if decrypted_passphrase: config['password'] = decrypted_passphrase

        try:
            exchange = exchange_class(config)
            exchange.check_required_credentials()
            logger.info(f"[SubID {user_sub_id}] Initialized CCXT exchange '{exchange_id}' for strategy '{strategy_instance.name}'.")
        except Exception as e:
            logger.error(f"[SubID {user_sub_id}] Failed to initialize CCXT for '{exchange_id}': {e}", exc_info=True)
            user_sub.status_message = f"Error initializing exchange: {str(e)[:100]}"
            user_sub.is_active = False
            db_session.commit()
            return {"status": "error", "message": f"Failed to initialize exchange: {e}"}

        # --- Strategy Execution Loop ---
        logger.info(f"[SubID {user_sub_id}] Task started for strategy '{strategy_instance.name}' on {symbol}.")

        while not self.request.is_terminated: # Check if task is terminated/revoked
            current_sub = db_session.query(UserStrategySubscription).filter(UserStrategySubscription.id == user_sub_id).first()
            if not current_sub or not current_sub.is_active or \
               (current_sub.expires_at and current_sub.expires_at <= datetime.datetime.utcnow()):
                logger.info(f"[SubID {user_sub_id}] Subscription expired or deactivated. Stopping task.")
                if current_sub and current_sub.is_active: # If expired but still marked active
                    current_sub.is_active = False
                    current_sub.status_message = "Stopped: Subscription expired or deactivated."
                    db_session.commit()
                break

            # Update status message in DB
            current_sub.status_message = f"Running - Last check: {datetime.datetime.utcnow().isoformat()}"
            db_session.commit()
            logger.debug(f"[SubID {user_sub_id}] Starting cycle for {symbol}@{timeframe}.")

            # --- Fetch Real-time Market Data ---
            market_data = None
            try:
                logger.debug(f"[SubID {user_sub_id}] Attempting to fetch OHLCV data for {symbol}@{timeframe}.")
                # Fetch OHLCV data - adjust limit based on strategy needs and exchange capabilities
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=10) # Fetch last 10 candles
                if ohlcv:
                    market_data = ohlcv
                    logger.debug(f"[SubID {user_sub_id}] Successfully fetched {len(ohlcv)} OHLCV candles.")
                else:
                    logger.warning(f"[SubID {user_sub_id}] No OHLCV data fetched.")

            except ccxt.BaseError as e:
                logger.error(f"[SubID {user_sub_id}] CCXT error fetching market data: {e}", exc_info=True)
                current_sub.status_message = f"Running - Data fetch CCXT error: {str(e)[:100]}"
                db_session.commit()
            except Exception as e:
                logger.error(f"[SubID {user_sub_id}] Unexpected error fetching market data: {e}", exc_info=True)
                current_sub.status_message = f"Running - Data fetch error: {str(e)[:100]}"
                db_session.commit()

            # --- Execute Strategy Signal ---
            if market_data: # Only execute if data was fetched
                try:
                    logger.debug(f"[SubID {user_sub_id}] Calling execute_live_signal for '{strategy_instance.name}'.")
                    # Pass market_data, exchange, db_session, and subscription ID to the strategy
                    # Note: Passing db_session directly might not be ideal in a task context if not managed properly.
                    # Strategies should ideally use a session provided or managed by the task.
                    # For now, we pass the task's session.
                    strategy_instance.execute_live_signal(market_data, exchange, db_session, user_sub_id)
                    # Update status message after successful execution cycle
                    current_sub.status_message = f"Running - Last executed: {datetime.datetime.utcnow().isoformat()}"
                    db_session.commit()
                    logger.debug(f"[SubID {user_sub_id}] Strategy execute_live_signal completed.")
                except Exception as e:
                    logger.error(f"[SubID {user_sub_id}] Error in strategy execute_live_signal: {e}", exc_info=True)
                    current_sub.status_message = f"Error in execution: {str(e)[:100]}"
                    db_session.commit()
            else:
                 logger.warning(f"[SubID {user_sub_id}] Skipping strategy execution due to no market data.")

            # --- Determine Sleep Duration ---
            sleep_duration_seconds = 60 # Default placeholder
            try:
                if timeframe.endswith('m'):
                    minutes = int(timeframe[:-1])
                    sleep_duration_seconds = max(1, minutes * 60 - 5) # Sleep slightly less than timeframe
                elif timeframe.endswith('h'):
                    hours = int(timeframe[:-1])
                    sleep_duration_seconds = max(60, hours * 3600 - 300) # Sleep slightly less than timeframe
                elif timeframe.endswith('d'):
                     days = int(timeframe[:-1])
                     sleep_duration_seconds = max(3600, days * 86400 - 3600) # Sleep slightly less than timeframe
                # Add more timeframe parsing as needed (e.g., 'w', 'M')
                logger.debug(f"[SubID {user_sub_id}] Calculated sleep duration: {sleep_duration_seconds}s based on timeframe {timeframe}.")
            except ValueError:
                logger.warning(f"[SubID {user_sub_id}] Could not parse timeframe '{timeframe}' for sleep calculation. Using default 60s.")
                sleep_duration_seconds = 60 # Fallback

            logger.debug(f"[SubID {user_sub_id}] Cycle complete. Sleeping for {sleep_duration_seconds}s.")
            time.sleep(sleep_duration_seconds) # Use time.sleep in task

    except Exception as e:
        logger.error(f"[SubID {user_sub_id}] Critical error in task: {e}", exc_info=True)
        # Attempt to update subscription status to error
        try:
            if db_session:
                sub_to_update = db_session.query(UserStrategySubscription).filter(UserStrategySubscription.id == user_sub_id).first()
                if sub_to_update:
                    sub_to_update.status_message = f"Critical Error: {str(e)[:100]}"
                    sub_to_update.is_active = False
                    db_session.commit()
        except Exception as db_err:
            logger.error(f"[SubID {user_sub_id}] DB error while updating status on critical task error: {db_err}")
        return {"status": "error", "message": f"Critical error: {e}"}
    finally:
        if db_session: db_session.close()
        logger.info(f"[SubID {user_sub_id}] Task finished.")
        return {"status": "completed", "message": "Task finished successfully."}

@celery_app.task(bind=True)
def run_backtest_task(self,
                      backtest_result_id: int, # Added backtest_result_id
                      user_id: int,
                      strategy_id: int,
                      custom_parameters: dict,
                      symbol: str,
                      timeframe: str,
                      start_date_str: str,
                      end_date_str: str,
                      initial_capital: float = 10000.0,
                      exchange_id: str = 'binance'
                     ):
    """
    Celery task to run a backtest.
    """
    db_session = None
    try:
        db_session = SessionLocal() # Get a new DB session for this task
        logger.info(f"Starting backtest task {self.request.id} for user {user_id}, strategy {strategy_id}.")

        # Call the core backtesting logic
        result = _perform_backtest_logic(
            db_session=db_session,
            backtest_result_id=backtest_result_id, # Pass backtest_result_id
            user_id=user_id,
            strategy_id=strategy_id,
            custom_parameters=custom_parameters,
            symbol=symbol,
            timeframe=timeframe,
            start_date_str=start_date_str,
            end_date_str=end_date_str,
            initial_capital=initial_capital,
            exchange_id=exchange_id
        )

        if result.get("status") == "success":
            logger.info(f"Backtest task {self.request.id} completed successfully for user {user_id}, strategy {strategy_id}.")
        else:
            logger.error(f"Backtest task {self.request.id} failed for user {user_id}, strategy {strategy_id}: {result.get('message')}")

        # TODO: Update backtest status in DB based on result

        return result # Return the result from the backtesting logic

    except Exception as e:
        logger.error(f"Critical error in backtest task {self.request.id} for user {user_id}, strategy {strategy_id}: {e}", exc_info=True)
        # TODO: Update backtest status in DB to indicate critical failure
        return {"status": "error", "message": f"Critical error during backtest: {e}"}
    finally:
        if db_session: db_session.close()
        logger.info(f"Backtest task {self.request.id} finished.")