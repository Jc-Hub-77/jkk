# trading_platform/strategies/rsi_divergence_strategy.py
import pandas as pd
import pandas_ta as ta
import numpy as np
import logging
from scipy.signal import find_peaks # For finding peaks and troughs

logger = logging.getLogger(__name__)

class RSIDivergenceStrategy:
    def __init__(self, symbol: str, timeframe: str, rsi_period: int = 14, 
                 lookback_period: int = 20, # For detecting divergence
                 peak_prominence: float = 0.5, # Prominence for find_peaks (adjust based on RSI scale)
                 capital: float = 10000, 
                 risk_per_trade_percent: float = 0.015, # 1.5%
                 stop_loss_percent: float = 0.02, # 2%
                 take_profit_percent: float = 0.04 # 4%
                 ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.rsi_period = int(rsi_period)
        self.lookback_period = int(lookback_period)
        self.peak_prominence = float(peak_prominence) 
        
        self.capital = float(capital)
        self.risk_per_trade_percent = float(risk_per_trade_percent)
        self.stop_loss_percent = float(stop_loss_percent)
        self.take_profit_percent = float(take_profit_percent)
        
        self.name = f"RSI ({self.rsi_period}) Divergence (Lookback: {self.lookback_period})"
        self.description = f"Identifies bullish and bearish divergences between price and RSI over a {self.lookback_period}-bar period."
        
        # Live trading state
        self.in_position = None # None, "LONG", "SHORT"
        self.entry_price = 0.0
        self.position_size_asset = 0.0
        
        self.price_precision = 8
        self.quantity_precision = 8
        logger.info(f"Initialized {self.name} for {self.symbol} on {self.timeframe}")

    @classmethod
    def get_parameters_definition(cls):
        return {
            "rsi_period": {"type": "int", "default": 14, "min": 5, "max": 50, "label": "RSI Period"},
            "lookback_period": {"type": "int", "default": 20, "min": 10, "max": 100, "label": "Divergence Lookback Period"},
            "peak_prominence": {"type": "float", "default": 0.5, "min": 0.1, "max": 10, "step":0.1, "label": "Peak Prominence (RSI)"},
            "capital": {"type": "float", "default": 10000, "min": 100, "label": "Initial Capital"},
            "risk_per_trade_percent": {"type": "float", "default": 1.5, "min": 0.1, "max": 10.0, "step":0.1, "label": "Risk per Trade (%)"},
            "stop_loss_percent": {"type": "float", "default": 2.0, "min": 0.1, "step": 0.1, "label": "Stop Loss % from Entry"},
            "take_profit_percent": {"type": "float", "default": 4.0, "min": 0.1, "step": 0.1, "label": "Take Profit % from Entry"}
        }

    def _calculate_rsi(self, df: pd.DataFrame):
        if 'close' not in df.columns:
            logger.error("DataFrame must contain 'close' column for RSI calculation.")
            return df
        df['rsi'] = ta.rsi(df['close'], length=self.rsi_period)
        return df

    def _find_divergence(self, price_series: pd.Series, rsi_series: pd.Series):
        # Ensure series have same index and length
        if not price_series.index.equals(rsi_series.index):
            logger.error("Price and RSI series must have the same index for divergence detection.")
            return None, None # No signal

        # Find price lows and highs (using negative for troughs with find_peaks)
        price_low_indices, _ = find_peaks(-price_series.values, prominence=price_series.std()*0.1) # Prominence relative to price std
        price_high_indices, _ = find_peaks(price_series.values, prominence=price_series.std()*0.1)

        # Find RSI lows and highs
        rsi_low_indices, _ = find_peaks(-rsi_series.values, prominence=self.peak_prominence)
        rsi_high_indices, _ = find_peaks(rsi_series.values, prominence=self.peak_prominence)

        # Bullish Divergence: Price LL, RSI HL (check last two significant lows)
        if len(price_low_indices) >= 2 and len(rsi_low_indices) >= 2:
            # Get last two price lows
            p_low1_idx, p_low2_idx = price_low_indices[-2], price_low_indices[-1]
            # Get corresponding or nearest RSI lows
            # This alignment is crucial and can be complex. For simplicity, find RSI lows *around* price lows.
            # A more robust method might search for RSI lows within a window of price lows.
            
            # Simplified: find last two RSI lows that are reasonably close to the end of the series
            rsi_l1_idx, rsi_l2_idx = -1, -1
            # Find RSI low corresponding to p_low2_idx (most recent price low)
            for r_idx in reversed(rsi_low_indices):
                if r_idx <= p_low2_idx: rsi_l2_idx = r_idx; break
            # Find RSI low corresponding to p_low1_idx
            for r_idx in reversed(rsi_low_indices):
                if r_idx <= p_low1_idx and r_idx < rsi_l2_idx : rsi_l1_idx = r_idx; break
            
            if rsi_l1_idx != -1 and rsi_l2_idx != -1:
                if price_series.iloc[p_low2_idx] < price_series.iloc[p_low1_idx] and \
                   rsi_series.iloc[rsi_l2_idx] > rsi_series.iloc[rsi_l1_idx]:
                    # Check if this divergence is recent (e.g., p_low2_idx is one of the last few bars)
                    if len(price_series) - p_low2_idx <= 3 : # Divergence confirmed on one of last 3 bars
                        return "bullish", p_low2_idx # Signal at the second price low

        # Bearish Divergence: Price HH, RSI LH (check last two significant highs)
        if len(price_high_indices) >= 2 and len(rsi_high_indices) >= 2:
            p_high1_idx, p_high2_idx = price_high_indices[-2], price_high_indices[-1]
            
            rsi_h1_idx, rsi_h2_idx = -1, -1
            for r_idx in reversed(rsi_high_indices):
                if r_idx <= p_high2_idx: rsi_h2_idx = r_idx; break
            for r_idx in reversed(rsi_high_indices):
                if r_idx <= p_high1_idx and r_idx < rsi_h2_idx: rsi_h1_idx = r_idx; break

            if rsi_h1_idx != -1 and rsi_h2_idx != -1:
                if price_series.iloc[p_high2_idx] > price_series.iloc[p_high1_idx] and \
                   rsi_series.iloc[rsi_h2_idx] < rsi_series.iloc[rsi_h1_idx]:
                    if len(price_series) - p_high2_idx <= 3:
                        return "bearish", p_high2_idx # Signal at the second price high
        
        return None, None


    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None):
        logger.info(f"Running backtest for {self.name} on {self.symbol}...")
        if historical_df.empty or len(historical_df) < self.lookback_period + self.rsi_period:
            return {"pnl": 0, "trades": [], "message": "Not enough historical data."}

        df = self._calculate_rsi(historical_df.copy())
        df.dropna(inplace=True)
        if df.empty or len(df) < self.lookback_period:
            return {"pnl": 0, "trades": [], "message": "Not enough data after RSI calculation."}

        trades_log = []
        current_position = None
        entry_price = 0.0
        position_size = 0.0
        balance = self.capital
        entry_time_ts = None

        for i in range(self.lookback_period -1, len(df)):
            window_df = df.iloc[i - self.lookback_period + 1 : i + 1]
            current_price = window_df['close'].iloc[-1]
            current_time_ts = window_df.index[-1].timestamp()

            # Exit conditions
            if current_position == "LONG":
                sl = entry_price * (1 - self.stop_loss_percent)
                tp = entry_price * (1 + self.take_profit_percent)
                if current_price <= sl or current_price >= tp:
                    exit_p = sl if current_price <= sl else tp
                    pnl = (exit_p - entry_price) * position_size
                    balance += pnl
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "long", "entry_price": entry_price, "exit_price": exit_p, "size": position_size, "pnl": pnl, "reason": "SL/TP"})
                    current_position = None
            elif current_position == "SHORT":
                sl = entry_price * (1 + self.stop_loss_percent)
                tp = entry_price * (1 - self.take_profit_percent)
                if current_price >= sl or current_price <= tp:
                    exit_p = sl if current_price >= sl else tp
                    pnl = (entry_price - exit_p) * position_size
                    balance += pnl
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "short", "entry_price": entry_price, "exit_price": exit_p, "size": position_size, "pnl": pnl, "reason": "SL/TP"})
                    current_position = None
            
            # Entry conditions
            if current_position is None:
                divergence_type, signal_idx = self._find_divergence(window_df['close'], window_df['rsi'])
                # Ensure signal is for the current bar (last bar in window)
                if signal_idx == len(window_df) - 1: 
                    if divergence_type == "bullish":
                        entry_price = current_price
                        entry_time_ts = current_time_ts
                        amount_to_risk = balance * self.risk_per_trade_percent
                        sl_dist = entry_price * self.stop_loss_percent
                        if sl_dist == 0: continue
                        position_size = amount_to_risk / sl_dist
                        trades_log.append({"entry_time": entry_time_ts, "exit_time": None, "type": "long", "entry_price": entry_price, "exit_price": None, "size": position_size, "pnl": None, "reason": "Bullish Divergence"})
                        current_position = "LONG"
                        logger.info(f"Backtest: LONG entry at {entry_price} on {window_df.index[-1]}")
                    elif divergence_type == "bearish":
                        entry_price = current_price
                        entry_time_ts = current_time_ts
                        amount_to_risk = balance * self.risk_per_trade_percent
                        sl_dist = entry_price * self.stop_loss_percent
                        if sl_dist == 0: continue
                        position_size = amount_to_risk / sl_dist
                        trades_log.append({"entry_time": entry_time_ts, "exit_time": None, "type": "short", "entry_price": entry_price, "exit_price": None, "size": position_size, "pnl": None, "reason": "Bearish Divergence"})
                        current_position = "SHORT"
                        logger.info(f"Backtest: SHORT entry at {entry_price} on {window_df.index[-1]}")
        
        final_pnl = balance - self.capital
        return {
            "pnl": final_pnl, "trades": [t for t in trades_log if t['exit_time'] is not None],
            "sharpe_ratio": 0.0, "max_drawdown": 0.0, # Placeholders
            "parameters_used": self.get_parameters_definition()
        }

    def _get_precisions_live(self, exchange_ccxt):
        if not hasattr(self, '_live_precisions_fetched_'):
            try:
                exchange_ccxt.load_markets()
                market = exchange_ccxt.market(self.symbol)
                self.price_precision = market['precision']['price']
                self.quantity_precision = market['precision']['amount']
                setattr(self, '_live_precisions_fetched_', True)
            except Exception as e: logger.error(f"Error fetching live precisions: {e}")

    def execute_live_signal(self, market_data_df: pd.DataFrame, exchange_ccxt):
        logger.debug(f"[{self.name}-{self.symbol}] Executing live signal check...")
        if market_data_df.empty or len(market_data_df) < self.lookback_period + self.rsi_period:
            logger.warning(f"[{self.name}-{self.symbol}] Insufficient market data for live signal.")
            return

        self._get_precisions_live(exchange_ccxt)
        df = self._calculate_rsi(market_data_df.copy())
        df.dropna(inplace=True)
        if len(df) < self.lookback_period: return

        # Consider only the most recent `lookback_period` of data for divergence detection
        analysis_window_df = df.iloc[-self.lookback_period:]
        current_price = analysis_window_df['close'].iloc[-1]

        # Exit logic
        if self.in_position == "LONG":
            sl = self.entry_price * (1 - self.stop_loss_percent)
            tp = self.entry_price * (1 + self.take_profit_percent)
            if current_price <= sl or current_price >= tp:
                reason = "SL" if current_price <= sl else "TP"
                logger.info(f"[{self.name}-{self.symbol}] Closing LONG at {current_price}. Reason: {reason}")
                # exchange_ccxt.create_market_sell_order(self.symbol, self.position_size_asset, params={'reduceOnly': True})
                logger.info(f"SIMULATED: Sell {self.position_size_asset} {self.symbol} to close LONG.")
                self.in_position = None
        elif self.in_position == "SHORT":
            sl = self.entry_price * (1 + self.stop_loss_percent)
            tp = self.entry_price * (1 - self.take_profit_percent)
            if current_price >= sl or current_price <= tp:
                reason = "SL" if current_price >= sl else "TP"
                logger.info(f"[{self.name}-{self.symbol}] Closing SHORT at {current_price}. Reason: {reason}")
                # exchange_ccxt.create_market_buy_order(self.symbol, self.position_size_asset, params={'reduceOnly': True})
                logger.info(f"SIMULATED: Buy {self.position_size_asset} {self.symbol} to close SHORT.")
                self.in_position = None

        # Entry logic
        if self.in_position is None:
            divergence_type, _ = self._find_divergence(analysis_window_df['close'], analysis_window_df['rsi'])
            if divergence_type:
                self.entry_price = current_price
                # Simplified sizing for live
                # amount_to_risk_usd = self.capital * self.risk_per_trade_percent
                # sl_distance_usd = self.entry_price * self.stop_loss_percent
                # if sl_distance_usd == 0: return
                # self.position_size_asset = exchange_ccxt.amount_to_precision(self.symbol, amount_to_risk_usd / sl_distance_usd)
                self.position_size_asset = 0.01 # Placeholder fixed size for simulation

                if divergence_type == "bullish":
                    logger.info(f"[{self.name}-{self.symbol}] Bullish RSI Divergence. LONG entry at {self.entry_price}. Size: {self.position_size_asset}")
                    # exchange_ccxt.create_market_buy_order(self.symbol, self.position_size_asset)
                    logger.info(f"SIMULATED: Market Buy {self.position_size_asset} {self.symbol}")
                    self.in_position = "LONG"
                    # TODO: Place SL/TP orders
                elif divergence_type == "bearish":
                    logger.info(f"[{self.name}-{self.symbol}] Bearish RSI Divergence. SHORT entry at {self.entry_price}. Size: {self.position_size_asset}")
                    # exchange_ccxt.create_market_sell_order(self.symbol, self.position_size_asset)
                    logger.info(f"SIMULATED: Market Sell {self.position_size_asset} {self.symbol}")
                    self.in_position = "SHORT"
                    # TODO: Place SL/TP orders
        logger.debug(f"[{self.name}-{self.symbol}] Live signal check complete. Position: {self.in_position}")

if __name__ == "__main__":
    # Dummy data for testing
    data_len = 200
    idx = pd.to_datetime([datetime.datetime(2023,1,1) + datetime.timedelta(hours=i) for i in range(data_len)])
    close_prices = 100 + np.cumsum(np.random.randn(data_len))
    dummy_data = pd.DataFrame({'close': close_prices}, index=idx)
    
    params = {"symbol": "BTC/USDT", "timeframe": "1h", "rsi_period": 14, "lookback_period": 30, "peak_prominence": 1.0}
    strategy = RSIDivergenceStrategy(**params)
    results = strategy.run_backtest(dummy_data)
    print(results)
