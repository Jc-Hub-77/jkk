# trading_platform/strategies/cpr_strategy.py
import datetime
import time
import pytz
import numpy as np
import pandas as pd
import ta # For indicators
import logging
import json
from sqlalchemy.orm import Session
from backend.models import Position, UserStrategySubscription, Order
# import ccxt # Handled by runner

logger = logging.getLogger(__name__)

class CPRStrategy:
    def __init__(self, symbol: str, timeframe: str, capital: float = 10000, # Timeframe here is for execution if finer than 1D
                 cpr_timeframe: str = '1d', # Always '1d' for CPR calculations
                 risk_percent: float = 1.0,
                 leverage: int = 3,
                 take_profit_percent: float = 0.8,
                 distance_threshold_percent: float = 0.24, # Renamed from DISTANCE_THRESHOLD
                 max_volatility_threshold_percent: float = 3.48, # Renamed
                 distance_condition_type: str = "Above", # "Above" or "Below" BC
                 sl_percent_from_entry: float = 3.5, # Renamed
                 pullback_percent_for_entry: float = 0.2, # Renamed
                 s1_bc_dist_thresh_low_percent: float = 2.2, # Renamed
                 s1_bc_dist_thresh_high_percent: float = 2.85, # Renamed
                 rsi_threshold_entry: float = 25.0, # Renamed
                 use_prev_day_cpr_tp_filter: bool = True,
                 reduced_tp_percent_if_filter: float = 0.2, # Renamed
                 use_monthly_cpr_filter_entry: bool = True # Renamed
                 ):
        self.name = "CPR Strategy"
        self.symbol = symbol
        self.timeframe = timeframe # Execution timeframe (e.g., 1h, 15m for checking exits)
        self.cpr_timeframe = '1d' # CPR calculations are based on daily data
        
        self.capital = capital
        self.risk_percent = risk_percent / 100.0 # Convert to decimal
        self.leverage = leverage
        self.take_profit_percent = take_profit_percent / 100.0
        self.distance_threshold_percent = distance_threshold_percent / 100.0
        self.max_volatility_threshold_percent = max_volatility_threshold_percent / 100.0
        self.distance_condition_type = distance_condition_type
        self.sl_percent_from_entry = sl_percent_from_entry / 100.0
        self.pullback_percent_for_entry = pullback_percent_for_entry / 100.0
        self.s1_bc_dist_thresh_low_percent = s1_bc_dist_thresh_low_percent / 100.0
        self.s1_bc_dist_thresh_high_percent = s1_bc_dist_thresh_high_percent / 100.0
        self.rsi_threshold_entry = rsi_threshold_entry
        self.use_prev_day_cpr_tp_filter = use_prev_day_cpr_tp_filter
        self.reduced_tp_percent_if_filter = reduced_tp_percent_if_filter / 100.0
        self.use_monthly_cpr_filter_entry = use_monthly_cpr_filter_entry

        # Internal state
        self.daily_cpr = None # Tuple: (P, TC, BC, R1, S1, R2, S2, R3, S3, R4, S4)
        self.weekly_cpr = None
        self.monthly_cpr = None
        self.daily_indicators = None # Pandas Series: EMA_21, EMA_50, RSI, MACD_Histo
        self.today_daily_open_utc = None # Today's 00:00 UTC open price
        self.data_prepared_for_utc_date = None # Tracks for which UTC date data is prepared
        self.monthly_cpr_filter_active = False

        # These will be fetched from exchange_ccxt
        self.price_precision = 8
        self.quantity_precision = 8
 
        logger.info(f"Initialized {self.name} for {self.symbol}.")
 
    @classmethod
    def get_parameters_definition(cls):
        return {
            "symbol": {"type": "string", "default": "BTC/USDT", "label": "Trading Symbol"},
            "timeframe": {"type": "timeframe", "default": "1h", "label": "Execution Timeframe"},
            # cpr_timeframe is fixed to '1d' internally
            "risk_percent": {"type": "float", "default": 1.0, "min": 0.1, "max": 5.0, "step": 0.1, "label": "Risk % per Trade"},
            "leverage": {"type": "int", "default": 3, "min": 1, "max": 20, "label": "Leverage"},
            "take_profit_percent": {"type": "float", "default": 0.8, "min": 0.1, "step": 0.1, "label": "Take Profit %"},
            "distance_threshold_percent": {"type": "float", "default": 0.24, "min": 0.01, "step": 0.01, "label": "DailyOpen to BC Distance Threshold %"},
            "max_volatility_threshold_percent": {"type": "float", "default": 3.48, "min": 0.1, "step": 0.1, "label": "Max S1-BC Volatility Threshold %"},
            "distance_condition_type": {"type": "select", "default": "Above", "options": ["Above", "Below"], "label": "DailyOpen vs BC Distance Condition"},
            "sl_percent_from_entry": {"type": "float", "default": 3.5, "min": 0.1, "step": 0.1, "label": "Stop Loss % from Entry"},
            "pullback_percent_for_entry": {"type": "float", "default": 0.2, "min": 0.0, "step": 0.01, "label": "Pullback % for Entry Target"},
            "s1_bc_dist_thresh_low_percent": {"type": "float", "default": 2.2, "min": 0.1, "step": 0.1, "label": "S1-BC Dist. Bypass Threshold Low %"},
            "s1_bc_dist_thresh_high_percent": {"type": "float", "default": 2.85, "min": 0.1, "step": 0.1, "label": "S1-BC Dist. Bypass Threshold High %"},
            "rsi_threshold_entry": {"type": "float", "default": 25.0, "min": 0, "max": 100, "label": "RSI Entry Threshold (Daily)"},
            "use_prev_day_cpr_tp_filter": {"type": "bool", "default": True, "label": "Use Prev. Day CPR for Reduced TP"},
            "reduced_tp_percent_if_filter": {"type": "float", "default": 0.2, "min": 0.05, "step": 0.01, "label": "Reduced TP % if Filter Active"},
            "use_monthly_cpr_filter_entry": {"type": "bool", "default": True, "label": "Use Monthly CPR Entry Filter"}
        }

    def _get_precisions(self, exchange_ccxt):
        # Simplified, assumes precisions are fetched once or are stable
        if not hasattr(self, '_precisions_fetched_'):
            try:
                exchange_ccxt.load_markets()
                market = exchange_ccxt.market(self.symbol)
                self.price_precision = market['precision']['price']
                self.quantity_precision = market['precision']['amount']
                setattr(self, '_precisions_fetched_', True)
                logger.info(f"Precisions for {self.symbol}: Price={self.price_precision}, Qty={self.quantity_precision}")
            except Exception as e:
                logger.error(f"Error fetching precision for {self.symbol}: {e}")
                # Keep defaults

    def _format_price(self, price, exchange_ccxt):
        self._get_precisions(exchange_ccxt)
        return exchange_ccxt.price_to_precision(self.symbol, price)

    def _format_quantity(self, quantity, exchange_ccxt):
        self._get_precisions(exchange_ccxt)
        return exchange_ccxt.amount_to_precision(self.symbol, quantity)

    def _calculate_cpr(self, prev_day_high, prev_day_low, prev_day_close):
        P = (prev_day_high + prev_day_low + prev_day_close) / 3
        TC = (prev_day_high + prev_day_low) / 2
        BC = (P - TC) + P
        if TC < BC: TC, BC = BC, TC # Ensure TC is Top Central, BC is Bottom Central
        R1 = (P * 2) - prev_day_low
        S1 = (P * 2) - prev_day_high
        R2 = P + (prev_day_high - prev_day_low)
        S2 = P - (prev_day_high - prev_day_low)
        R3 = P + 2 * (prev_day_high - prev_day_low) # Original script had R3 = H + 2 * (P - L) which is different
        S3 = P - 2 * (prev_day_high - prev_day_low) # Original script had S3 = L - 2 * (H - P)
        R4 = R3 + (R2 - R1)
        S4 = S3 - (S1 - S2)
        return P, TC, BC, R1, S1, R2, S2, R3, S3, R4, S4

    def _calculate_indicators(self, df_daily: pd.DataFrame):
        if df_daily.empty or len(df_daily) < 50: # Need enough data for EMAs
            logger.warning("Not enough daily data to calculate all indicators.")
            return None
        indicators = pd.Series(dtype='float64')
        price_data = df_daily['close'] # Use 'close' from DataFrame
        indicators['EMA_21'] = ta.ema(price_data, length=21).iloc[-1]
        indicators['EMA_50'] = ta.ema(price_data, length=50).iloc[-1]
        indicators['RSI'] = ta.rsi(price_data, length=14).iloc[-1]
        macd_obj = ta.macd(price_data, fast=12, slow=26, signal=9)
        if macd_obj is not None and not macd_obj.empty:
             indicators['MACD_Histo'] = macd_obj.iloc[-1, 1] # MACDh_12_26_9
             indicators['MACD'] = macd_obj.iloc[-1, 0]       # MACD_12_26_9
             indicators['MACD_Signal'] = macd_obj.iloc[-1, 2] # MACDs_12_26_9
        else:
            indicators['MACD_Histo'] = indicators['MACD'] = indicators['MACD_Signal'] = np.nan
        return indicators.fillna(0)

    # --- Methods for Live Execution (called by execute_live_signal) ---
    def _prepare_daily_data_live(self, exchange_ccxt):
        logger.info(f"[{self.name}] Preparing daily data (CPR, indicators) for {datetime.datetime.now(pytz.utc).date()}")
        now_utc = datetime.datetime.now(pytz.utc)
        today_utc_date = now_utc.date()
        yesterday_utc_date = today_utc_date - datetime.timedelta(days=1)
        last_month_start = (today_utc_date.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        last_week_start = today_utc_date - datetime.timedelta(days=today_utc_date.weekday() + 7) # Start of last week (Monday)

        try:
            # Fetch daily data for indicators and daily CPR
            # Need enough data for EMA_50, so fetch at least 60 days
            ohlcv_daily = exchange_ccxt.fetch_ohlcv(self.symbol, '1d', limit=60)
            if not ohlcv_daily or len(ohlcv_daily) < 2:
                logger.warning(f"[{self.name}] Not enough daily data fetched for {self.symbol}.")
                self.data_prepared_for_utc_date = None
                return

            df_daily = pd.DataFrame(ohlcv_daily, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_daily['timestamp'] = pd.to_datetime(df_daily['timestamp'], unit='ms')
            df_daily['date'] = df_daily['timestamp'].dt.date

            # Ensure data is sorted by timestamp
            df_daily = df_daily.sort_values('timestamp')

            # Get previous day's data for daily CPR
            prev_day_data = df_daily.iloc[-2] # Second to last row is previous day

            self.daily_cpr = self._calculate_cpr(prev_day_data['high'], prev_day_data['low'], prev_day_data['close'])
            self.daily_indicators = self._calculate_indicators(df_daily.iloc[:-1]) # Calculate indicators on data *before* today

            # Get today's daily open (first candle of today)
            today_ohlcv = exchange_ccxt.fetch_ohlcv(self.symbol, '1d', limit=1)
            if today_ohlcv:
                 self.today_daily_open_utc = today_ohlcv[0][1] # Open price of the current day's candle
            else:
                 logger.warning(f"[{self.name}] Could not fetch today's daily open for {self.symbol}.")
                 self.today_daily_open_utc = None # Or handle appropriately

            # Fetch weekly data for weekly CPR (need data for the last completed week)
            ohlcv_weekly = exchange_ccxt.fetch_ohlcv(self.symbol, '1w', limit=2) # Need last completed week
            if ohlcv_weekly and len(ohlcv_weekly) > 1:
                 prev_week_data = ohlcv_weekly[-2] # Second to last is previous week
                 self.weekly_cpr = self._calculate_cpr(prev_week_data[2], prev_week_data[3], prev_week_data[4])
            else:
                 logger.warning(f"[{self.name}] Not enough weekly data fetched for {self.symbol}.")
                 self.weekly_cpr = None

            # Fetch monthly data for monthly CPR (need data for the last completed month)
            ohlcv_monthly = exchange_ccxt.fetch_ohlcv(self.symbol, '1M', limit=2) # Need last completed month
            if ohlcv_monthly and len(ohlcv_monthly) > 1:
                 prev_month_data = ohlcv_monthly[-2] # Second to last is previous month
                 self.monthly_cpr = self._calculate_cpr(prev_month_data[2], prev_month_data[3], prev_month_data[4])
            else:
                 logger.warning(f"[{self.name}] Not enough monthly data fetched for {self.symbol}.")
                 self.monthly_cpr = None

            # Determine monthly_cpr_filter_active
            self.monthly_cpr_filter_active = False
            if self.use_monthly_cpr_filter_entry and self.monthly_cpr and self.today_daily_open_utc is not None:
                 # Check if today's daily open is within monthly CPR range (P, TC, BC)
                 monthly_P, monthly_TC, monthly_BC, *_ = self.monthly_cpr
                 if monthly_BC <= self.today_daily_open_utc <= monthly_TC:
                      self.monthly_cpr_filter_active = True
                      logger.info(f"[{self.name}] Monthly CPR filter is ACTIVE.")


            self.data_prepared_for_utc_date = today_utc_date
            logger.info(f"[{self.name}] Daily data prepared for {self.data_prepared_for_utc_date}.")
            logger.debug(f"[{self.name}] Daily CPR: {self.daily_cpr}")
            logger.debug(f"[{self.name}] Daily Indicators: {self.daily_indicators.to_dict()}")
            logger.debug(f"[{self.name}] Today Daily Open (UTC): {self.today_daily_open_utc}")
            logger.debug(f"[{self.name}] Weekly CPR: {self.weekly_cpr}")
            logger.debug(f"[{self.name}] Monthly CPR: {self.monthly_cpr}")


        except Exception as e:
            logger.error(f"[{self.name}] Error preparing daily data: {e}")
            self.data_prepared_for_utc_date = None # Mark as not prepared on error


    def _check_entry_conditions_live(self, db_session: Session, subscription_id: int, exchange_ccxt):
        logger.info(f"[{self.name}] Checking entry conditions for {self.symbol}.")

        if self.daily_cpr is None or self.daily_indicators is None or self.today_daily_open_utc is None:
            logger.warning(f"[{self.name}] Daily data not prepared. Skipping entry check.")
            return

        P, TC, BC, R1, S1, R2, S2, R3, S3, R4, S4 = self.daily_cpr
        daily_open = self.today_daily_open_utc
        rsi_daily = self.daily_indicators.get('RSI', np.nan)

        if np.isnan(rsi_daily):
             logger.warning(f"[{self.name}] Daily RSI not available. Skipping entry check.")
             return

        # Condition 1: Daily Open vs BC distance
        bc_distance_percent = abs(daily_open - BC) / BC * 100.0 if BC != 0 else float('inf')
        distance_condition_met = False
        if self.distance_condition_type == "Above" and daily_open > BC and bc_distance_percent >= self.distance_threshold_percent * 100:
            distance_condition_met = True
        elif self.distance_condition_type == "Below" and daily_open < BC and bc_distance_percent >= self.distance_threshold_percent * 100:
            distance_condition_met = True

        if not distance_condition_met:
            logger.debug(f"[{self.name}] Entry condition failed: Daily Open ({daily_open}) vs BC ({BC}) distance ({bc_distance_percent:.2f}%) not meeting threshold ({self.distance_threshold_percent * 100:.2f}%) or condition type ({self.distance_condition_type}).")
            return

        # Condition 2: S1-BC Volatility Threshold
        s1_bc_distance_percent = abs(S1 - BC) / BC * 100.0 if BC != 0 else float('inf')
        if not (self.s1_bc_dist_thresh_low_percent * 100 <= s1_bc_distance_percent <= self.s1_bc_dist_thresh_high_percent * 100):
             logger.debug(f"[{self.name}] Entry condition failed: S1-BC distance ({s1_bc_distance_percent:.2f}%) outside threshold range ({self.s1_bc_dist_thresh_low_percent * 100:.2f}% - {self.s1_bc_dist_thresh_high_percent * 100:.2f}%).")
             return

        # Condition 3: Daily RSI Threshold
        if rsi_daily > self.rsi_threshold_entry:
             logger.debug(f"[{self.name}] Entry condition failed: Daily RSI ({rsi_daily:.2f}) above threshold ({self.rsi_threshold_entry:.2f}).")
             return

        # Condition 4: Monthly CPR Filter
        if self.use_monthly_cpr_filter_entry and self.monthly_cpr_filter_active:
             logger.debug(f"[{self.name}] Entry condition failed: Monthly CPR filter is active.")
             return

        # All initial conditions met, now check for pullback entry
        try:
            ticker = exchange_ccxt.fetch_ticker(self.symbol)
            current_price = ticker['last']
        except Exception as e:
            logger.error(f"[{self.name}] Error fetching ticker for {self.symbol}: {e}")
            return

        # Calculate target entry price based on pullback from Daily Open
        if self.distance_condition_type == "Above": # Looking for pullback DOWN to target
             target_entry_price = daily_open * (1 - self.pullback_percent_for_entry)
             if current_price <= target_entry_price:
                  logger.info(f"[{self.name}] Entry conditions met. Current price ({current_price}) is at or below pullback target ({target_entry_price}). Attempting LONG entry.")
                  self._open_long_position_live(db_session, subscription_id, current_price, exchange_ccxt)
             else:
                  logger.debug(f"[{self.name}] Entry conditions met, but waiting for pullback. Current price ({current_price}) > target ({target_entry_price}).")

        elif self.distance_condition_type == "Below": # Looking for pullback UP to target
             target_entry_price = daily_open * (1 + self.pullback_percent_for_entry)
             if current_price >= target_entry_price:
                  logger.info(f"[{self.name}] Entry conditions met. Current price ({current_price}) is at or above pullback target ({target_entry_price}). Attempting LONG entry.")
                  self._open_long_position_live(db_session, subscription_id, current_price, exchange_ccxt)
             else:
                  logger.debug(f"[{self.name}] Entry conditions met, but waiting for pullback. Current price ({current_price}) < target ({target_entry_price}).")


    def _open_long_position_live(self, db_session: Session, subscription_id: int, entry_price: float, exchange_ccxt):
        logger.info(f"[{self.name}] Attempting to open LONG position for subscription {subscription_id} at {entry_price}")

        try:
            # Calculate position size
            # Risk amount = Total Capital * Risk Percent
            risk_amount = self.capital * self.risk_percent
            # Stop Loss distance in price = Entry Price * SL Percent from Entry
            sl_distance_price = entry_price * self.sl_percent_from_entry
            # Stop Loss price = Entry Price - SL distance (for LONG)
            stop_loss_price = entry_price - sl_distance_price
            # Position size in quote currency (USDT) = Risk Amount / SL distance in price
            position_size_quote = risk_amount / sl_distance_price if sl_distance_price != 0 else 0
            # Position size in base currency (e.g., BTC) = Position size in quote / Entry Price
            position_size_asset = (position_size_quote / entry_price) * self.leverage if entry_price != 0 else 0

            if position_size_asset <= 0:
                logger.warning(f"[{self.name}] Calculated position size is zero or negative ({position_size_asset:.8f}). Skipping order placement.")
                return

            # Format quantity and price according to exchange precision
            formatted_quantity = self._format_quantity(position_size_asset, exchange_ccxt)
            formatted_entry_price = self._format_price(entry_price, exchange_ccxt)
            formatted_stop_loss_price = self._format_price(stop_loss_price, exchange_ccxt)

            logger.info(f"[{self.name}] Calculated: Risk Amount=${risk_amount:.2f}, SL Price={stop_loss_price:.8f}, Position Size (Asset)={position_size_asset:.8f}, Formatted Qty={formatted_quantity}, Formatted Entry Price={formatted_entry_price}, Formatted SL Price={formatted_stop_loss_price}")

            # Place Market Order for Entry
            order = exchange_ccxt.create_market_buy_order(self.symbol, formatted_quantity)
            logger.info(f"[{self.name}] Market BUY order placed: {order}")

            # Assume order is filled immediately for simplicity in this conceptual implementation
            # In a real system, you'd need to confirm fill and get actual fill price/quantity
            actual_filled_price = float(order.get('price', entry_price)) # Use actual fill price if available
            actual_filled_quantity = float(order.get('amount', formatted_quantity)) # Use actual fill quantity

            # Calculate Take Profit Price
            take_profit_price = actual_filled_price * (1 + self.take_profit_percent)
            formatted_take_profit_price = self._format_price(take_profit_price, exchange_ccxt)
            logger.info(f"[{self.name}] Calculated TP Price: {take_profit_price:.8f}, Formatted TP Price: {formatted_take_profit_price}")

            # Create Position in DB
            new_position = Position(
                subscription_id=subscription_id,
                symbol=self.symbol,
                side="long",
                entry_price=actual_filled_price,
                amount=actual_filled_quantity,
                is_open=True,
                open_time=datetime.datetime.now(pytz.utc)
            )
            db_session.add(new_position)
            db_session.commit()
            db_session.refresh(new_position) # Get the generated ID

            logger.info(f"[{self.name}] Position created in DB: ID {new_position.id}")

            # Place Stop Loss and Take Profit Orders (Limit or Stop-Limit depending on exchange)
            sl_order = exchange_ccxt.create_stop_loss_limit_order(
                self.symbol,
                'sell', # To close a long position
                formatted_quantity,
                formatted_stop_loss_price, # Limit price (can be same as stop price or slightly lower)
                {'stopPrice': formatted_stop_loss_price} # Stop price
            )
            logger.info(f"[{self.name}] Stop Loss order placed: {sl_order}")
            
            # Create Order entry for SL in DB
            new_sl_order = Order(
                subscription_id=subscription_id,
                position_id=new_position.id, # Link to the position
                order_id=sl_order.get('id'),
                symbol=self.symbol,
                order_type=sl_order.get('type'),
                side=sl_order.get('side'),
                amount=sl_order.get('amount'),
                price=sl_order.get('price'),
                cost=sl_order.get('cost'),
                filled=sl_order.get('filled', 0.0),
                remaining=sl_order.get('remaining', sl_order.get('amount')),
                status=sl_order.get('status', 'open'),
                created_at=datetime.datetime.utcnow(),
                updated_at=datetime.datetime.utcnow()
            )
            db_session.add(new_sl_order)

            tp_order = exchange_ccxt.create_limit_sell_order(
                self.symbol,
                formatted_quantity,
                formatted_take_profit_price
            )
            logger.info(f"[{self.name}] Take Profit order placed: {tp_order}")
            
            # Create Order entry for TP in DB
            new_tp_order = Order(
                subscription_id=subscription_id,
                position_id=new_position.id, # Link to the position
                order_id=tp_order.get('id'),
                symbol=self.symbol,
                order_type=tp_order.get('type'),
                side=tp_order.get('side'),
                amount=tp_order.get('amount'),
                price=tp_order.get('price'),
                cost=tp_order.get('cost'),
                filled=tp_order.get('filled', 0.0),
                remaining=tp_order.get('remaining', tp_order.get('amount')),
                status=tp_order.get('status', 'open'),
                created_at=datetime.datetime.utcnow(),
                updated_at=datetime.datetime.utcnow()
            )
            db_session.add(new_tp_order)
            db_session.commit() # Commit both SL and TP orders

            logger.info(f"[{self.name}] LONG position opened successfully for subscription {subscription_id}.")

        except Exception as e:
            logger.error(f"[{self.name}] Error opening LONG position: {e}")
            # Need robust error handling: cancel orders if one fails, update DB state, etc.
            # No longer setting self.in_position = False here, as state is DB-driven


    def _check_exit_conditions_live(self, db_session: Session, subscription_id: int, current_position_db: Position, exchange_ccxt):
        logger.info(f"[{self.name}] Checking exit conditions for {self.symbol}.")

        if current_position_db is None or not current_position_db.is_open:
            logger.warning(f"[{self.name}] No open position found in DB for subscription {subscription_id}. State mismatch?")
            self.in_position = False # Correct internal state
            return

        # Fetch associated SL/TP orders from the database
        sl_order_db = db_session.query(Order).filter(
            Order.position_id == current_position_db.id,
            Order.order_type.in_(['stop_market', 'stop_limit', 'stop']), # Assuming these types for SL
            Order.status == 'open'
        ).first()
        tp_order_db = db_session.query(Order).filter(
            Order.position_id == current_position_db.id,
            Order.order_type.in_(['limit', 'take_profit_limit', 'take_profit']), # Assuming these types for TP
            Order.status == 'open'
        ).first()

        try:
            # Fetch open orders from the exchange to check their actual status
            exchange_open_orders = exchange_ccxt.fetch_open_orders(self.symbol)
            exchange_open_order_ids = {order['id'] for order in exchange_open_orders}

            # Check if SL order was filled (i.e., not in open orders anymore)
            if sl_order_db and sl_order_db.order_id not in exchange_open_order_ids:
                logger.info(f"[{self.name}] Stop Loss order {sl_order_db.order_id} filled. Closing position.")
                # Update order status in DB
                sl_order_db.status = 'closed'
                sl_order_db.updated_at = datetime.datetime.utcnow()
                db_session.add(sl_order_db)
                db_session.commit()
                self._close_position_live(db_session, subscription_id, current_position_db, "Stop Loss Hit", exchange_ccxt)
                return

            # Check if TP order was filled
            if tp_order_db and tp_order_db.order_id not in exchange_open_order_ids:
                logger.info(f"[{self.name}] Take Profit order {tp_order_db.order_id} filled. Closing position.")
                # Update order status in DB
                tp_order_db.status = 'closed'
                tp_order_db.updated_at = datetime.datetime.utcnow()
                db_session.add(tp_order_db)
                db_session.commit()
                self._close_position_live(db_session, subscription_id, current_position_db, "Take Profit Hit", exchange_ccxt)
                return

        except Exception as e:
            logger.error(f"[{self.name}] Error checking order status from exchange: {e}")
            # Continue to check other exit conditions even if order status check fails

        # Check for manual exit conditions (e.g., BC Hit, End of Day)
        try:
            ticker = exchange_ccxt.fetch_ticker(self.symbol)
            current_price = ticker['last']
        except Exception as e:
            logger.error(f"[{self.name}] Error fetching ticker for {self.symbol}: {e}")
            return # Cannot check price-based exit conditions without current price

        P, TC, BC, R1, S1, R2, S2, R3, S3, R4, S4 = self.daily_cpr # Assuming daily_cpr is available

        # Manual Exit Condition: Price hits BC
        if current_position_db.side == "long" and current_price <= BC:
            logger.info(f"[{self.name}] Price ({current_price}) hit or crossed below BC ({BC}). Closing LONG position.")
            self._close_position_live(db_session, subscription_id, current_position_db, "BC Hit", exchange_ccxt)
            return

        # Manual Exit Condition: End of Day (e.g., close position near market close UTC)
        now_utc = datetime.datetime.now(pytz.utc)
        # Example: Close position 5 minutes before UTC midnight
        if now_utc.hour == 23 and now_utc.minute >= 55:
             logger.info(f"[{self.name}] End of day approaching. Closing position.")
             self._close_position_live(db_session, subscription_id, current_position_db, "End of Day", exchange_ccxt)
             return

        logger.debug(f"[{self.name}] No exit conditions met.")


    def _close_position_live(self, db_session: Session, subscription_id: int, current_position_db: Position, reason: str, exchange_ccxt):
        logger.info(f"[{self.name}] Attempting to close position for subscription {subscription_id} due to: {reason}")

        if current_position_db is None or not current_position_db.is_open:
            logger.warning(f"[{self.name}] No open position found in DB for subscription {subscription_id} to close.")
            self.in_position = False # Correct internal state
            return

        try:
            # Fetch associated SL/TP orders from the database
            sl_order_db = db_session.query(Order).filter(
                Order.position_id == current_position_db.id,
                Order.order_type.in_(['stop_market', 'stop_limit', 'stop']),
                Order.status == 'open'
            ).first()
            tp_order_db = db_session.query(Order).filter(
                Order.position_id == current_position_db.id,
                Order.order_type.in_(['limit', 'take_profit_limit', 'take_profit']),
                Order.status == 'open'
            ).first()

            # Cancel any open SL/TP orders on the exchange
            if sl_order_db:
                try:
                    exchange_ccxt.cancel_order(sl_order_db.order_id, self.symbol)
                    logger.info(f"[{self.name}] Cancelled SL order {sl_order_db.order_id}")
                    sl_order_db.status = 'canceled'
                    sl_order_db.updated_at = datetime.datetime.utcnow()
                    db_session.add(sl_order_db)
                except Exception as e:
                    logger.warning(f"[{self.name}] Could not cancel SL order {sl_order_db.order_id}: {e}")

            if tp_order_db:
                try:
                    exchange_ccxt.cancel_order(tp_order_db.order_id, self.symbol)
                    logger.info(f"[{self.name}] Cancelled TP order {tp_order_db.order_id}")
                    tp_order_db.status = 'canceled'
                    tp_order_db.updated_at = datetime.datetime.utcnow()
                    db_session.add(tp_order_db)
                except Exception as e:
                    logger.warning(f"[{self.name}] Could not cancel TP order {tp_order_db.order_id}: {e}")
            db_session.commit() # Commit cancellation status updates

            # Place Market Order to Close Position
            # For a LONG position, place a SELL order
            side = 'sell' if current_position_db.side == 'long' else 'buy'
            formatted_quantity = self._format_quantity(current_position_db.amount, exchange_ccxt)

            close_order = exchange_ccxt.create_market_order(self.symbol, side, formatted_quantity)
            logger.info(f"[{self.name}] Market {side.upper()} order placed to close position: {close_order}")

            # Assume order is filled immediately
            actual_close_price = float(close_order.get('price', exchange_ccxt.fetch_ticker(self.symbol)['last'])) # Use actual fill price or current ticker
            actual_closed_quantity = float(close_order.get('amount', formatted_quantity))

            # Update Position in DB
            current_position_db.is_open = False
            current_position_db.close_time = datetime.datetime.now(pytz.utc)
            current_position_db.close_price = actual_close_price
            # Calculate PnL (simplified)
            if current_position_db.side == 'long':
                 pnl = (actual_close_price - current_position_db.entry_price) * actual_closed_quantity
            else: # short
                 pnl = (current_position_db.entry_price - actual_close_price) * actual_closed_quantity
            current_position_db.pnl = pnl

            db_session.commit()
            logger.info(f"[{self.name}] Position ID {current_position_db.id} closed in DB. PnL: {pnl:.2f}")

            # Reset internal state
            self.in_position = False
            self.entry_price = 0.0
            self.position_quantity = 0.0
            self.stop_loss_order_id = None
            self.take_profit_order_id = None
            self.last_entry_attempt_utc_time = None # Allow new entry attempts after closing

            logger.info(f"[{self.name}] Position closed successfully for subscription {subscription_id}.")

        except Exception as e:
            logger.error(f"[{self.name}] Error closing position: {e}")
            # Need robust error handling: log error, potentially retry, etc.


    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None):
        logger.warning(f"[{self.name}] Backtesting for CPR strategy is complex and requires careful daily setup simulation. This is a simplified conceptual outline.")
        # TODO: Implement detailed backtesting logic that simulates the daily data prep and entry/exit checks.
        # This involves iterating through historical_df (expected to be daily),
        # calculating CPRs for each day based on *its* previous day/week/month,
        # calculating indicators for each day based on *its* previous data,
        # and then applying the entry/exit logic.
        return {"pnl": 0, "trades": [], "message": "CPR backtesting not fully implemented yet."}

    def execute_live_signal(self, db_session: Session, subscription_id: int, market_data_df: pd.DataFrame = None, exchange_ccxt=None):
        """
        Executes the strategy's logic based on new market data for a live subscription.
        Manages position state in the database.
        """
        if not exchange_ccxt:
            logger.error(f"[{self.name}-{self.symbol}] Exchange instance not provided.")
            return

        logger.debug(f"[{self.name}-{self.symbol}] Executing live signal check for subscription {subscription_id}...")

        # Fetch the current position from the database
        current_position_db = db_session.query(Position).filter(
            Position.subscription_id == subscription_id,
            Position.is_open == True
        ).first()

        current_position_type = current_position_db.side if current_position_db else None # "long", "short", or None
        # entry_price and position_size_asset are now managed via current_position_db if in position
        # entry_price = current_position_db.entry_price if current_position_db else 0.0
        # position_size_asset = current_position_db.amount if current_position_db else 0.0

        self._get_precisions(exchange_ccxt) # Ensure precisions are loaded

        now_utc = datetime.datetime.now(pytz.utc)

        # Prepare daily data if it's a new day (around 00:00 UTC)
        if self.data_prepared_for_utc_date != now_utc.date():
            if now_utc.hour == 0 and now_utc.minute < 15: # Window for daily prep
                self._prepare_daily_data_live(exchange_ccxt)
            elif now_utc.hour >= 0 and self.data_prepared_for_utc_date is None : # First run, try to prepare
                 self._prepare_daily_data_live(exchange_ccxt)


        if self.data_prepared_for_utc_date == now_utc.date(): # Only proceed if data for today is ready
            if current_position_type is None:
                # Entry checks are time-sensitive (around 00:00 UTC as per script)
                if now_utc.hour == 0 and now_utc.minute < 5: # Strict window
                     if self.last_entry_attempt_utc_time is None or \
                        (now_utc - self.last_entry_attempt_utc_time).total_seconds() > 300: # 5 min cooldown
                         self._check_entry_conditions_live(db_session, subscription_id, exchange_ccxt)
                         self.last_entry_attempt_utc_time = now_utc
            else: # In position
                self._check_exit_conditions_live(db_session, subscription_id, current_position_db, exchange_ccxt)
        else:
            logger.debug(f"[{self.name}-{self.symbol}] Daily data for {now_utc.date()} not yet prepared. Current prepared date: {self.data_prepared_for_utc_date}")

        logger.debug(f"[{self.name}-{self.symbol}] Live signal execution cycle finished.")
