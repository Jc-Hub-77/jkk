# trading_platform/strategies/top_gainers_losers_macd_strategy.py
import datetime
import logging
import time
import pandas as pd
import ta
import numpy as np
# import ccxt # Handled by runner

logger = logging.getLogger(__name__)

class TopGainersLosersMACDStrategy:
    def __init__(self, symbol: str, timeframe: str, capital: float = 10000, 
                 top_n_symbols: int = 3, # Reduced default for manageability
                 scan_interval_minutes: int = 15,
                 macd_fast_period: int = 12, # More common MACD defaults
                 macd_slow_period: int = 26,
                 macd_signal_period: int = 9,
                 leverage: int = 3, # User parameter
                 stop_loss_percent: float = 5.0, # User parameter (e.g., 5 for 5%)
                 risk_per_trade_percent: float = 1.0, # User parameter (e.g., 1 for 1%)
                 min_volume_usdt_24h: float = 1000000, 
                 min_candles_for_signal: int = 50, # Adjusted from 144
                 symbol_eval_period_hours: int = 6, 
                 symbol_min_volatility_percent: float = 3.0 # Adjusted from 5.0
                 ):
        self.name = "Top Gainers/Losers MACD Entry"
        self.timeframe = timeframe # Timeframe for MACD signals on selected symbols

        self.capital_total_allocation = capital 
        self.top_n_symbols = top_n_symbols
        self.scan_interval_minutes = scan_interval_minutes
        self.macd_fast_period = macd_fast_period
        self.macd_slow_period = macd_slow_period
        self.macd_signal_period = macd_signal_period
        self.leverage = leverage # For exchange.set_leverage() if applicable
        self.stop_loss_percent = stop_loss_percent / 100.0 # Convert to decimal
        self.risk_per_trade_percent = risk_per_trade_percent / 100.0 # Convert to decimal
        
        self.min_volume_usdt_24h = min_volume_usdt_24h
        self.min_candles_for_signal = min_candles_for_signal
        self.symbol_eval_period_hours = symbol_eval_period_hours
        self.symbol_min_volatility_percent = symbol_min_volatility_percent / 100.0

        # Internal state
        # Stores data for symbols currently being tracked and potentially traded
        # Value: { 'entry_scan_time': datetime, 'volatility_ok_until': datetime, 
        #           'in_position': None/"LONG"/"SHORT", 'entry_price': float, 'position_qty': float, 
        #           'sl_order_id': str, 'tp_order_id': str (if TP is used) }
        self.tracked_symbols_state = {} 
        self.last_scan_time_utc = None
        
        self.precisions_cache = {} # symbol -> {'price': p, 'amount': q}

        logger.info(f"Initialized {self.name}. Scan interval: {self.scan_interval_minutes} mins. Top N: {self.top_n_symbols}.")

    @classmethod
    def get_parameters_definition(cls):
        return {
            "top_n_symbols": {"type": "int", "default": 3, "min": 1, "max": 10, "label": "Top N Gainers/Losers to Track"},
            "scan_interval_minutes": {"type": "int", "default": 15, "min": 5, "max": 120, "label": "Scan Interval (minutes)"},
            "macd_fast_period": {"type": "int", "default": 12, "min": 2, "label": "MACD Fast Period"},
            "macd_slow_period": {"type": "int", "default": 26, "min": 2, "label": "MACD Slow Period"},
            "macd_signal_period": {"type": "int", "default": 9, "min": 2, "label": "MACD Signal Period"},
            "leverage": {"type": "int", "default": 3, "min": 1, "max": 20, "label": "Leverage (Informational, set on exchange)"},
            "stop_loss_percent": {"type": "float", "default": 5.0, "min": 0.1, "step": 0.1, "label": "Stop Loss % from Entry"},
            "risk_per_trade_percent": {"type": "float", "default": 1.0, "min": 0.1, "step": 0.1, "label": "Risk per Trade (% of allocated capital per symbol)"},
            "min_volume_usdt_24h": {"type": "float", "default": 1000000, "min": 10000, "label": "Min 24h Volume (USDT) for Scan"},
            "min_candles_for_signal": {"type": "int", "default": 50, "min": 30, "label": "Min Candles for MACD Signal"},
            "symbol_eval_period_hours": {"type": "int", "default": 6, "min":1, "label": "Symbol Volatility Check Period (Hours)"},
            "symbol_min_volatility_percent": {"type": "float", "default": 3.0, "min":0.1, "label": "Min Volatility % in Eval Period to Keep Symbol"}
        }

    def _get_precisions(self, symbol, exchange_ccxt):
        if symbol not in self.precisions_cache:
            try:
                exchange_ccxt.load_markets() # Ensure markets are loaded
                market = exchange_ccxt.market(symbol)
                self.precisions_cache[symbol] = {
                    'price': market['precision']['price'],
                    'amount': market['precision']['amount']
                }
                logger.info(f"Precisions for {symbol}: {self.precisions_cache[symbol]}")
            except Exception as e:
                logger.error(f"Error fetching precision for {symbol}: {e}")
                self.precisions_cache[symbol] = {'price': 8, 'amount': 8} # Fallback defaults
        return self.precisions_cache[symbol]

    def _format_quantity(self, symbol, quantity, exchange_ccxt):
        prec = self._get_precisions(symbol, exchange_ccxt)
        return exchange_ccxt.amount_to_precision(symbol, quantity)

    def _format_price(self, symbol, price, exchange_ccxt):
        prec = self._get_precisions(symbol, exchange_ccxt)
        return exchange_ccxt.price_to_precision(symbol, price)

    def _calculate_position_size_asset(self, symbol, entry_price, stop_loss_price, exchange_ccxt, capital_for_this_trade_usdt):
        if entry_price == 0 or abs(entry_price - stop_loss_price) == 0: return 0
        
        # This capital_for_this_trade_usdt is the portion of total strategy capital allocated to this one symbol/trade
        usdt_to_risk_on_this_trade = capital_for_this_trade_usdt * self.risk_per_trade_percent
        
        price_diff_per_unit_at_sl = abs(entry_price - stop_loss_price)
        
        quantity_asset = usdt_to_risk_on_this_trade / price_diff_per_unit_at_sl
        
        return float(self._format_quantity(symbol, quantity_asset, exchange_ccxt))

    def _scan_top_gainers_losers(self, exchange_ccxt):
        logger.info(f"[{self.name}] Scanning for top gainers/losers...")
        # ... (scanner logic from previous version, seems okay, ensure market type filtering is robust) ...
        # For brevity, assuming it returns a list of dicts: [{'symbol': 'BTC/USDT', ...}, ...]
        try:
            all_tickers = exchange_ccxt.fetch_tickers()
            usdt_perps = []
            for symbol, data in all_tickers.items():
                market = exchange_ccxt.market(symbol)
                if market.get('quote', '').upper() == 'USDT' and \
                   (market.get('type', '').lower() == 'future' or market.get('swap', False)) and \
                   market.get('active', True) and \
                   data.get('quoteVolume', 0) >= self.min_volume_usdt_24h and \
                   data.get('percentage') is not None:
                    usdt_perps.append({
                        'symbol': symbol,
                        'priceChangePercent': data['percentage'],
                        'volume_usdt': data['quoteVolume']
                    })
            if not usdt_perps: return []
            sorted_by_change = sorted(usdt_perps, key=lambda x: x['priceChangePercent'], reverse=True)
            top_gainers = sorted_by_change[:self.top_n_symbols]
            top_losers = sorted_by_change[-self.top_n_symbols:]
            return top_gainers + top_losers
        except Exception as e:
            logger.error(f"[{self.name}] Error scanning tickers: {e}"); return []


    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None):
        logger.warning(f"[{self.name}] Backtesting is not applicable for this strategy type.")
        return {"pnl": 0, "trades": [], "message": "Backtesting not implemented."}

    def execute_live_signal(self, market_data_df: pd.DataFrame = None, exchange_ccxt=None):
        if not exchange_ccxt: logger.error(f"[{self.name}] Exchange instance not provided."); return
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        # --- Scanner Logic ---
        if self.last_scan_time_utc is None or \
           (now_utc - self.last_scan_time_utc) >= datetime.timedelta(minutes=self.scan_interval_minutes):
            scanned_symbols_info = self._scan_top_gainers_losers(exchange_ccxt)
            self.last_scan_time_utc = now_utc
            
            current_tracked_symbols = set(self.tracked_symbols_state.keys())
            newly_scanned_symbols = set(item['symbol'] for item in scanned_symbols_info)

            # Add new symbols
            for item in scanned_symbols_info:
                symbol = item['symbol']
                if symbol not in self.tracked_symbols_state:
                    self.tracked_symbols_state[symbol] = {
                        'entry_scan_time': now_utc,
                        'volatility_ok_until': now_utc + datetime.timedelta(hours=self.symbol_eval_period_hours),
                        'in_position': None, 'entry_price': 0.0, 'position_qty': 0.0,
                        'sl_order_id': None, 'tp_order_id': None # TP not used in original script
                    }
                    logger.info(f"[{self.name}] Added new symbol to track: {symbol}")
            
            # Remove symbols no longer in scan (unless in position - handle that separately if needed)
            for symbol in list(current_tracked_symbols - newly_scanned_symbols):
                if not self.tracked_symbols_state[symbol]['in_position']: # Only remove if not in position
                    del self.tracked_symbols_state[symbol]
                    logger.info(f"[{self.name}] Removed symbol (no longer in scan): {symbol}")
            logger.info(f"[{self.name}] Currently tracking: {list(self.tracked_symbols_state.keys())}")


        # --- Prune Stale/Non-Volatile Symbols ---
        # ... (Pruning logic from previous version, seems okay) ...
        # This should iterate over self.tracked_symbols_state

        # --- Trading Logic for each tracked symbol ---
        if not self.tracked_symbols_state: logger.info(f"[{self.name}] No symbols to trade."); return

        try: balance_info = exchange_ccxt.fetch_balance(); usdt_balance = balance_info.get('USDT', {}).get('free', 0)
        except Exception as e: logger.error(f"Error fetching balance: {e}"); return

        # Allocate capital per symbol (can be refined)
        capital_per_symbol = (self.capital_total_allocation / len(self.tracked_symbols_state)) if self.tracked_symbols_state else 0
        if capital_per_symbol == 0: logger.warning("No capital per symbol."); return


        for symbol, state in list(self.tracked_symbols_state.items()): # Iterate copy if modifying
            try:
                ohlcv = exchange_ccxt.fetch_ohlcv(symbol, self.timeframe, limit=self.min_candles_for_signal + 5)
                if not ohlcv or len(ohlcv) < self.min_candles_for_signal: continue
                
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                
                macd_df = df.ta.macd(fast=self.macd_fast_period, slow=self.macd_slow_period, signal=self.macd_signal_period, append=False)
                if macd_df is None or macd_df.empty or len(macd_df) < 2: continue
                
                hist_col = f'MACDh_{self.macd_fast_period}_{self.macd_slow_period}_{self.macd_signal_period}'
                if hist_col not in macd_df.columns: continue

                latest_hist = macd_df[hist_col].iloc[-1]; prev_hist = macd_df[hist_col].iloc[-2]
                latest_close = df['close'].iloc[-1]; latest_open = df['open'].iloc[-1]
                is_green = latest_close > latest_open; is_red = latest_close < latest_open

                # Check current position for THIS symbol (conceptual)
                # In a real system, you'd fetch this from exchange or maintain robust internal state
                # For now, using self.tracked_symbols_state[symbol]['in_position']
                
                # Exit logic (simplified: if SL hit, assume it's handled by exchange order)
                # A more robust check would query open orders and positions.

                # Entry Logic
                if not state['in_position']:
                    long_signal = latest_hist > 0 and prev_hist <= 0 and is_green
                    short_signal = latest_hist < 0 and prev_hist >= 0 and is_red
                    
                    entry_side = None
                    if long_signal: entry_side = 'buy'
                    elif short_signal: entry_side = 'sell'

                    if entry_side:
                        sl_price = latest_close * (1 - self.stop_loss_percent) if entry_side == 'buy' else latest_close * (1 + self.stop_loss_percent)
                        qty = self._calculate_position_size_asset(symbol, latest_close, sl_price, exchange_ccxt, capital_per_symbol)
                        
                        if qty > 0:
                            logger.info(f"[{self.name}] {entry_side.upper()} SIGNAL for {symbol} at {latest_close}. Qty: {qty}, SL: {sl_price}")
                            try:
                                # 1. Cancel any existing orders for this symbol (precaution)
                                # exchange_ccxt.cancel_all_orders(symbol)
                                # 2. Place market order
                                # market_order = exchange_ccxt.create_market_order(symbol, entry_side, qty)
                                # filled_qty = float(market_order['filled'])
                                # filled_price = float(market_order['price']) # or average price
                                # state['in_position'] = "LONG" if entry_side == 'buy' else "SHORT"
                                # state['entry_price'] = filled_price
                                # state['position_qty'] = filled_qty
                                # 3. Place SL order
                                # sl_order_side = 'sell' if entry_side == 'buy' else 'buy'
                                # sl_order_params = {'stopPrice': self._format_price(symbol, sl_price, exchange_ccxt), 'reduceOnly': True}
                                # sl_order = exchange_ccxt.create_order(symbol, 'stop_market', sl_order_side, filled_qty, params=sl_order_params)
                                # state['sl_order_id'] = sl_order['id']
                                logger.info(f"SIMULATED: Market {entry_side} {qty} {symbol} @ {latest_close}. SL @ {sl_price}")
                                state['in_position'] = "LONG" if entry_side == 'buy' else "SHORT" # Update state
                                state['entry_price'] = latest_close # Simulate fill at current price
                                state['position_qty'] = qty
                            except Exception as e:
                                logger.error(f"[{self.name}] Error placing {entry_side} order for {symbol}: {e}")
            except Exception as e:
                logger.error(f"[{self.name}] Error processing symbol {symbol} in trading logic: {e}", exc_info=True)
        
        logger.debug(f"[{self.name}] Live signal execution cycle finished.")
