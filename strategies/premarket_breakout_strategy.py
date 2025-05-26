# trading_platform/strategies/premarket_breakout_strategy.py
import datetime
import pytz # For timezone handling
import pandas as pd
import logging
# import ccxt # Will be used by the live runner and backtester

logger = logging.getLogger(__name__)

class PremarketBreakoutStrategy:
    def __init__(self, symbol: str, timeframe: str, capital: float = 10000, 
                 risk_per_trade_percent: float = 0.01, # Changed from 0.25 to 0.01 (1%) for consistency
                 leverage: int = 10, 
                 stop_loss_percent: float = 0.01, 
                 take_profit_percent: float = 0.03, 
                 premarket_max_deviation_percent: float = 0.02, 
                 premarket_start_hour_est: int = 7, premarket_start_minute_est: int = 30,
                 premarket_end_hour_est: int = 9, premarket_end_minute_est: int = 0,
                 market_open_hour_est: int = 9, market_open_minute_est: int = 30
                 ):
        self.name = "Premarket Breakout Strategy"
        self.symbol = symbol
        self.timeframe = timeframe 
        self.capital = capital # Initial capital for backtest, or allocated capital for this strategy instance
        self.risk_per_trade_percent = risk_per_trade_percent # As decimal, e.g., 0.01 for 1%
        self.leverage = leverage # Note: Leverage amplifies P&L and risk. Sizing should account for this.
        self.stop_loss_percent = stop_loss_percent
        self.take_profit_percent = take_profit_percent
        self.premarket_max_deviation_percent = premarket_max_deviation_percent
        
        self.est_timezone = pytz.timezone('US/Eastern')
        self.premarket_start_time_est = datetime.time(premarket_start_hour_est, premarket_start_minute_est)
        self.premarket_end_time_est = datetime.time(premarket_end_hour_est, premarket_end_minute_est)
        self.market_open_time_est = datetime.time(market_open_hour_est, market_open_minute_est)

        self.premarket_high = None
        self.premarket_low = None
        self.max_deviation_high = None
        self.max_deviation_low = None
        
        self.last_trade_time_utc = None 
        self.initialized_for_day_utc_date = None 
        
        self.quantity_precision = None 
        self.price_precision = None   

        # Live trading state
        self.in_position = None # None, "LONG", "SHORT"
        self.current_position_qty_asset = 0.0
        self.current_entry_price = 0.0
        self.current_sl_order_id = None
        self.current_tp_order_id = None

        logger.info(f"Initialized {self.name} for {self.symbol} with capital ${self.capital:.2f}")

    @classmethod
    def get_parameters_definition(cls):
        return {
            "symbol": {"type": "string", "default": "BTC/USDT", "label": "Trading Symbol (e.g., BTC/USDT)"},
            "timeframe": {"type": "string", "default": "15m", "label": "Execution Kline Interval (e.g., 1m, 5m, 15m, 1h)"},
            "capital": {"type": "float", "default": 10000, "min": 100, "label": "Initial Capital (USD for backtest)"},
            "risk_per_trade_percent": {"type": "float", "default": 1.0, "min": 0.1, "max": 5.0, "step": 0.1, "label": "Risk per Trade (% of Capital)"},
            "leverage": {"type": "int", "default": 10, "min": 1, "max": 100, "label": "Leverage (for futures)"},
            "stop_loss_percent": {"type": "float", "default": 1.0, "min": 0.1, "max": 10.0, "step": 0.1, "label": "Stop Loss (% from entry)"},
            "take_profit_percent": {"type": "float", "default": 3.0, "min": 0.1, "max": 20.0, "step": 0.1, "label": "Take Profit (% from entry)"},
            "premarket_max_deviation_percent": {"type": "float", "default": 2.0, "min": 0.1, "max": 10.0, "step": 0.1, "label": "Max Price Deviation from Premarket (%)"},
            "premarket_start_hour_est": {"type": "int", "default": 7, "min":0, "max":23, "label": "Premarket Start Hour (EST)"},
            "premarket_start_minute_est": {"type": "int", "default": 30, "min":0, "max":59, "label": "Premarket Start Minute (EST)"},
            "premarket_end_hour_est": {"type": "int", "default": 9, "min":0, "max":23, "label": "Premarket End Hour (EST)"},
            "premarket_end_minute_est": {"type": "int", "default": 0, "min":0, "max":59, "label": "Premarket End Minute (EST)"},
            "market_open_hour_est": {"type": "int", "default": 9, "min":0, "max":23, "label": "Market Open Hour (EST)"},
            "market_open_minute_est": {"type": "int", "default": 30, "min":0, "max":59, "label": "Market Open Minute (EST)"},
        }

    def _get_current_utc_time(self): return datetime.datetime.now(pytz.utc)
    def _get_current_est_time(self): return self._get_current_utc_time().astimezone(self.est_timezone)

    def _get_ccxt_exchange_details(self, exchange_ccxt, symbol_override=None):
        # Allow overriding symbol for generic helpers if needed, else use self.symbol
        sym = symbol_override if symbol_override else self.symbol
        
        # Check if precisions for this symbol are already fetched
        if sym in getattr(self, '_precisions_cache', {}):
            self.price_precision = self._precisions_cache[sym]['price']
            self.quantity_precision = self._precisions_cache[sym]['amount']
            return

        if not hasattr(self, '_precisions_cache'):
            self._precisions_cache = {}

        try:
            exchange_ccxt.load_markets()
            market = exchange_ccxt.market(sym)
            self.price_precision = market['precision']['price']
            self.quantity_precision = market['precision']['amount']
            self._precisions_cache[sym] = {'price': self.price_precision, 'amount': self.quantity_precision}
            logger.info(f"Precisions for {sym}: Price={self.price_precision}, Qty={self.quantity_precision}")
        except Exception as e:
            logger.error(f"Error fetching symbol info for {sym} via CCXT: {e}")
            self.price_precision = self.price_precision or 8 
            self.quantity_precision = self.quantity_precision or 8
            self._precisions_cache[sym] = {'price': self.price_precision, 'amount': self.quantity_precision}


    def _format_quantity(self, quantity, exchange_ccxt, symbol_override=None):
        sym = symbol_override if symbol_override else self.symbol
        self._get_ccxt_exchange_details(exchange_ccxt, sym)
        return exchange_ccxt.amount_to_precision(sym, quantity)

    def _format_price(self, price, exchange_ccxt, symbol_override=None):
        sym = symbol_override if symbol_override else self.symbol
        self._get_ccxt_exchange_details(exchange_ccxt, sym)
        return exchange_ccxt.price_to_precision(sym, price)

    def _calculate_position_size_asset(self, entry_price_usdt, current_balance_usdt, exchange_ccxt):
        if entry_price_usdt == 0: return 0
        
        usdt_to_risk_per_trade = current_balance_usdt * self.risk_per_trade_percent
        
        # Stop loss distance in USDT from entry price
        sl_distance_usdt = entry_price_usdt * self.stop_loss_percent
        if sl_distance_usdt == 0:
            logger.warning(f"[{self.name}-{self.symbol}] Stop loss distance is zero. Cannot calculate position size.")
            return 0
            
        # Quantity of asset such that if SL is hit, usdt_to_risk_per_trade is lost (before leverage)
        # For futures, the actual P&L is (exit_price - entry_price) * quantity_asset * contract_size (if applicable)
        # Assuming contract_size = 1 for crypto perps (value of 1 unit of base asset)
        # Quantity = (USD to Risk) / (USD movement per unit of asset to SL)
        quantity_asset = usdt_to_risk_per_trade / sl_distance_usdt
        
        # Apply leverage to the quantity (this means we control more asset value with same capital)
        # Or, more correctly, leverage allows a smaller margin for the same position value.
        # The risk calculation above is based on capital at risk. Leverage amplifies this.
        # If risk_per_trade_percent is on *account equity*, then leverage is already factored in by exchange.
        # If risk_per_trade_percent is on *position value*, then leverage is used to achieve that position value.
        # The original script's `position_size_usdt = total_equity * self.risk_per_trade * self.leverage`
        # implies the position value is leveraged.
        
        # Let's assume self.capital is the margin used, and risk is on this margin.
        # Position Value (USDT) = Margin * Leverage
        # Quantity = (Margin * Leverage) / Entry Price
        # If SL hit, loss on margin = (SL_distance_percent_of_entry_price * Position_Value_USDT) / Leverage
        # We want this loss_on_margin to be self.capital * self.risk_per_trade_percent
        # So, (SL_percent * Margin * Leverage) / Leverage = self.capital * risk_percent
        # SL_percent * Margin = self.capital * risk_percent
        # Margin = (self.capital * risk_percent) / SL_percent
        # This 'Margin' is the actual capital exposed to the SL distance.
        
        # Let's use the common definition: Risk % of total available capital for the trade.
        # Quantity = (Capital_to_Risk_USD / Price_per_unit) / SL_percent_of_price
        # This is quantity if leverage=1. With leverage, you can trade more.
        # The key is that the USD value of the loss at SL should be `current_balance_usdt * self.risk_per_trade_percent`.
        # Loss_USD = abs(entry_price - sl_price) * quantity_asset
        # quantity_asset = (current_balance_usdt * self.risk_per_trade_percent) / abs(entry_price - (entry_price * (1-SL%)))
        # quantity_asset = (current_balance_usdt * self.risk_per_trade_percent) / (entry_price * SL%)
        
        # This is the quantity of the base asset.
        quantity_asset_calculated = (current_balance_usdt * self.risk_per_trade_percent) / (entry_price_usdt * self.stop_loss_percent)
        
        return float(self._format_quantity(quantity_asset_calculated, exchange_ccxt))


    def _fetch_premarket_levels_ccxt(self, current_utc_date, exchange_ccxt):
        # ... (content from previous version, seems okay) ...
        current_est_datetime_for_pm_calc = datetime.datetime(current_utc_date.year, current_utc_date.month, current_utc_date.day, tzinfo=pytz.utc).astimezone(self.est_timezone)
        pm_start_est = current_est_datetime_for_pm_calc.replace(hour=self.premarket_start_time_est.hour, minute=self.premarket_start_time_est.minute, second=0, microsecond=0)
        pm_end_est = current_est_datetime_for_pm_calc.replace(hour=self.premarket_end_time_est.hour, minute=self.premarket_end_time_est.minute, second=0, microsecond=0)
        pm_start_utc_ms = int(pm_start_est.timestamp() * 1000)
        pm_end_utc_ms = int(pm_end_est.timestamp() * 1000)
        logger.info(f"Fetching premarket klines for {self.symbol} between {pm_start_est} EST and {pm_end_est} EST (UTCms: {pm_start_utc_ms} to {pm_end_utc_ms})")
        try:
            # Fetch 1-minute klines for granularity. Max limit for CCXT fetch_ohlcv is often around 500-1500.
            # Premarket is 1.5 hours = 90 minutes.
            ohlcv = exchange_ccxt.fetch_ohlcv(self.symbol, '1m', since=pm_start_utc_ms, limit=100) # Fetch 100 1m candles
            premarket_klines = [k for k in ohlcv if pm_start_utc_ms <= k[0] < pm_end_utc_ms]
            if not premarket_klines:
                logger.warning(f"No klines found for premarket period for {self.symbol} on {current_utc_date.strftime('%Y-%m-%d')}")
                return None, None
            highs = [float(k[2]) for k in premarket_klines]; lows = [float(k[3]) for k in premarket_klines]
            return max(highs), min(lows)
        except Exception as e:
            logger.error(f"Error fetching premarket klines for {self.symbol}: {e}"); return None, None

    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None): # Added htf for signature consistency
        logger.info(f"Running backtest for {self.name} on {self.symbol} from {historical_df.index.min()} to {historical_df.index.max()}")
        trades_log = []
        current_position_type = None # None, "LONG", "SHORT"
        trade_entry_price = 0.0
        position_qty = 0.0
        balance = self.capital

        if historical_df.index.tz is None: historical_df.index = historical_df.index.tz_localize('UTC')
        
        for date_utc, daily_data_utc in historical_df.groupby(historical_df.index.date):
            current_est_day_start = datetime.datetime(date_utc.year, date_utc.month, date_utc.day, tzinfo=pytz.utc).astimezone(self.est_timezone)
            pm_start_est = current_est_day_start.replace(hour=self.premarket_start_time_est.hour, minute=self.premarket_start_time_est.minute)
            pm_end_est = current_est_day_start.replace(hour=self.premarket_end_time_est.hour, minute=self.premarket_end_time_est.minute)
            market_open_est = current_est_day_start.replace(hour=self.market_open_time_est.hour, minute=self.market_open_time_est.minute)
            pm_start_utc = pm_start_est.astimezone(pytz.utc); pm_end_utc = pm_end_est.astimezone(pytz.utc); market_open_utc = market_open_est.astimezone(pytz.utc)

            premarket_period_data = daily_data_utc[(daily_data_utc.index >= pm_start_utc) & (daily_data_utc.index < pm_end_utc)]
            if premarket_period_data.empty: continue
            
            pm_high = premarket_period_data['high'].max(); pm_low = premarket_period_data['low'].min()
            max_dev_high = pm_high * (1 + self.premarket_max_deviation_percent)
            max_dev_low = pm_low * (1 - self.premarket_max_deviation_percent)
            market_hours_data = daily_data_utc[daily_data_utc.index >= market_open_utc]

            for index_utc, row in market_hours_data.iterrows():
                current_price = row['close']; current_time_ts = index_utc.timestamp()

                if current_position_type == "LONG":
                    sl = trade_entry_price * (1 - self.stop_loss_percent); tp = trade_entry_price * (1 + self.take_profit_percent)
                    if current_price <= sl or current_price >= tp or (pm_low <= current_price <= pm_high):
                        exit_p = sl if current_price <= sl else (tp if current_price >= tp else current_price)
                        pnl = (exit_p - trade_entry_price) * position_qty * self.leverage # Simplified PnL with leverage
                        balance += pnl
                        trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "long", "entry_price": trade_entry_price, "exit_price": exit_p, "size": position_qty, "pnl": pnl, "reason": "SL/TP/Re-entry"})
                        current_position_type = None
                elif current_position_type == "SHORT":
                    sl = trade_entry_price * (1 + self.stop_loss_percent); tp = trade_entry_price * (1 - self.take_profit_percent)
                    if current_price >= sl or current_price <= tp or (pm_low <= current_price <= pm_high):
                        exit_p = sl if current_price >= sl else (tp if current_price <= tp else current_price)
                        pnl = (trade_entry_price - exit_p) * position_qty * self.leverage
                        balance += pnl
                        trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "short", "entry_price": trade_entry_price, "exit_price": exit_p, "size": position_qty, "pnl": pnl, "reason": "SL/TP/Re-entry"})
                        current_position_type = None
                
                if not current_position_type:
                    # Simplified position sizing for backtest
                    # In a real backtest, available balance for risk calc would change after each trade.
                    # Here, we use a fraction of the initial self.capital for simplicity or current balance.
                    sl_dist_for_sizing = current_price * self.stop_loss_percent
                    if sl_dist_for_sizing == 0: continue
                    qty_to_trade = (balance * self.risk_per_trade_percent) / sl_dist_for_sizing

                    if current_price > pm_high and current_price <= max_dev_high:
                        trade_entry_price = current_price; entry_time_ts = current_time_ts; position_qty = qty_to_trade
                        trades_log.append({"entry_time": entry_time_ts, "exit_time": None, "type": "long", "entry_price": trade_entry_price, "size": position_qty, "pnl": None, "reason": "Breakout High"})
                        current_position_type = "LONG"
                    elif current_price < pm_low and current_price >= max_dev_low:
                        trade_entry_price = current_price; entry_time_ts = current_time_ts; position_qty = qty_to_trade
                        trades_log.append({"entry_time": entry_time_ts, "exit_time": None, "type": "short", "entry_price": trade_entry_price, "size": position_qty, "pnl": None, "reason": "Breakout Low"})
                        current_position_type = "SHORT"
            
            if current_position_type: # EOD close
                last_price = market_hours_data['close'].iloc[-1] if not market_hours_data.empty else trade_entry_price
                exit_reason = "EOD Close"
                if current_position_type == "LONG": pnl = (last_price - trade_entry_price) * position_qty * self.leverage
                else: pnl = (trade_entry_price - last_price) * position_qty * self.leverage
                balance += pnl
                trades_log.append({"entry_time": entry_time_ts, "exit_time": market_hours_data.index[-1].timestamp(), "type": current_position_type.lower(), "entry_price": trade_entry_price, "exit_price": last_price, "size": position_qty, "pnl": pnl, "reason": exit_reason})
                current_position_type = None
        
        final_pnl = balance - self.capital
        return {"pnl": final_pnl, "trades": [t for t in trades_log if t['exit_time'] is not None], "sharpe_ratio": 0.0, "max_drawdown": 0.0}

    def execute_live_signal(self, market_data_df: pd.DataFrame = None, exchange_ccxt=None):
        if not exchange_ccxt: logger.error(f"[{self.name}-{self.symbol}] Exchange instance not provided."); return
        self._get_ccxt_exchange_details(exchange_ccxt)
        now_utc = self._get_current_utc_time(); now_est = now_utc.astimezone(self.est_timezone); today_utc_date = now_utc.date()

        if now_est.weekday() >= 5: # Skip weekends
            if self.initialized_for_day_utc_date: self.initialized_for_day_utc_date = None; self.premarket_high = None
            return

        if self.initialized_for_day_utc_date != today_utc_date and now_est.time() >= self.premarket_end_time_est:
            logger.info(f"[{self.name}-{self.symbol}] Initializing for day {today_utc_date} (EST: {now_est.date()}).")
            pm_h, pm_l = self._fetch_premarket_levels_ccxt(today_utc_date, exchange_ccxt)
            if pm_h and pm_l:
                self.premarket_high = pm_h; self.premarket_low = pm_l
                self.max_deviation_high = pm_h * (1 + self.premarket_max_deviation_percent)
                self.max_deviation_low = pm_l * (1 - self.premarket_max_deviation_percent)
                self.initialized_for_day_utc_date = today_utc_date; self.last_trade_time_utc = None
                logger.info(f"PM levels for {today_utc_date}: H={pm_h}, L={pm_l}")
            else:
                logger.warning(f"Could not fetch PM levels for {today_utc_date}. Skipping today.")
                self.initialized_for_day_utc_date = today_utc_date; self.premarket_high = None; return

        if not self.premarket_high or self.initialized_for_day_utc_date != today_utc_date or now_est.time() < self.market_open_time_est:
            logger.debug("Not ready/market not open."); return

        try: current_price = float(exchange_ccxt.fetch_ticker(self.symbol)['last'])
        except Exception as e: logger.error(f"Error fetching ticker: {e}"); return

        if self.last_trade_time_utc and (now_utc - self.last_trade_time_utc) < datetime.timedelta(minutes=1): return # Cooldown

        # Fetch current balance for position sizing
        try: account_balance_usdt = float(exchange_ccxt.fetch_balance()['USDT']['free'])
        except Exception as e: logger.error(f"Error fetching balance: {e}"); account_balance_usdt = self.capital # Fallback

        # Simplified position check (a real one would query exchange)
        if self.in_position: # Check exit for existing position
            side_to_close = 'sell' if self.in_position == "LONG" else 'buy'
            sl_price = self.current_entry_price * (1 - self.stop_loss_percent if self.in_position == "LONG" else 1 + self.stop_loss_percent)
            tp_price = self.current_entry_price * (1 + self.take_profit_percent if self.in_position == "LONG" else 1 - self.take_profit_percent)
            
            exit_now = False; reason = ""
            if self.in_position == "LONG" and (current_price <= sl_price or current_price >= tp_price or (self.premarket_low <= current_price <= self.premarket_high)):
                exit_now = True; reason = "SL/TP/Re-entry Long"
            elif self.in_position == "SHORT" and (current_price >= sl_price or current_price <= tp_price or (self.premarket_low <= current_price <= self.premarket_high)):
                exit_now = True; reason = "SL/TP/Re-entry Short"

            if exit_now:
                logger.info(f"[{self.name}-{self.symbol}] Closing {self.in_position} at {current_price}. Reason: {reason}")
                # exchange_ccxt.create_market_order(self.symbol, side_to_close, self.current_position_qty_asset, params={'reduceOnly': True})
                # exchange_ccxt.cancel_order(self.current_sl_order_id, self.symbol) if self.current_sl_order_id else None
                # exchange_ccxt.cancel_order(self.current_tp_order_id, self.symbol) if self.current_tp_order_id else None
                logger.info(f"SIMULATED: Market {side_to_close} {self.current_position_qty_asset} {self.symbol}. Cancel SL/TP.")
                self.in_position = None; self.current_position_qty_asset = 0.0; self.last_trade_time_utc = now_utc
            return # Don't check for new entry if we were in position and just exited or still in it

        # Check for new entry
        if not self.in_position:
            qty_asset = self._calculate_position_size_asset(current_price, account_balance_usdt, exchange_ccxt)
            if qty_asset <= 0: logger.warning("Calculated quantity is zero or less."); return

            if current_price > self.premarket_high and current_price <= self.max_deviation_high:
                logger.info(f"[{self.name}-{self.symbol}] LONG Entry Signal. Price: {current_price}, Qty: {qty_asset}")
                # order = exchange_ccxt.create_market_buy_order(self.symbol, qty_asset)
                # self.current_entry_price = float(order['price']) # Or avgFillPrice
                # self.in_position = "LONG"; self.current_position_qty_asset = float(order['filled'])
                # sl = self._format_price(self.current_entry_price * (1 - self.stop_loss_percent), exchange_ccxt)
                # tp = self._format_price(self.current_entry_price * (1 + self.take_profit_percent), exchange_ccxt)
                # self.current_sl_order_id = exchange_ccxt.create_order(self.symbol, 'stop_market', 'sell', self.current_position_qty_asset, params={'stopPrice': sl, 'reduceOnly':True})['id']
                # self.current_tp_order_id = exchange_ccxt.create_order(self.symbol, 'take_profit_market', 'sell', self.current_position_qty_asset, params={'stopPrice': tp, 'reduceOnly':True})['id']
                logger.info(f"SIMULATED: Market Buy {qty_asset} {self.symbol} @ {current_price}. Place SL/TP.")
                self.in_position = "LONG"; self.current_entry_price = current_price; self.current_position_qty_asset = qty_asset; self.last_trade_time_utc = now_utc
            
            elif current_price < self.premarket_low and current_price >= self.max_deviation_low:
                logger.info(f"[{self.name}-{self.symbol}] SHORT Entry Signal. Price: {current_price}, Qty: {qty_asset}")
                # Similar logic for SHORT entry with SL/TP
                logger.info(f"SIMULATED: Market Sell {qty_asset} {self.symbol} @ {current_price}. Place SL/TP.")
                self.in_position = "SHORT"; self.current_entry_price = current_price; self.current_position_qty_asset = qty_asset; self.last_trade_time_utc = now_utc
        
        logger.debug(f"[{self.name}-{self.symbol}] Live signal check complete. Position: {self.in_position}")
