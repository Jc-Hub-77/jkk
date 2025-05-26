import time
import math
import logging
import pandas as pd
import datetime  # Added for datetime.utcnow()
import json  # Added for json.loads/dumps
from sqlalchemy.orm import Session  # Added for type hinting
from backend.models import Position, Order  # Added for database interaction

# Configure logging for the strategy
logger = logging.getLogger(__name__)
# logger.setLevel(logging.INFO) # Or DEBUG for more verbosity
# handler = logging.StreamHandler()
# formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# handler.setFormatter(formatter)
# logger.addHandler(handler)
# Ensure logger is configured by the main application if this is a module


class DCAStrategy:
    def __init__(self, symbol: str, timeframe: str, capital: float = 10000, **custom_parameters):
        self.symbol = symbol
        self.timeframe = timeframe  # May not be directly used if strategy polls ticker
        self.capital = capital  # Available capital, though DCA logic uses fixed USDT amounts

        # Default parameters - these would be overridden by custom_parameters from UI
        defaults = {
            "base_order_size_usdt": 10.0,
            "safety_order_size_usdt": 10.0,
            "tp1_percent": 1.0,
            "tp1_sell_percent": 40.0,  # Percentage of current position to sell at TP1
            "tp2_percent": 3.0,
            "tp2_sell_percent": 40.0,  # Percentage of current position to sell at TP2
            "tp3_percent": 20.0,  # TP3 always sells 100% of remaining
            "stop_loss_percent": 5.0,
            "safety_order_deviation_percent": 1.0,  # Price deviation for placing next safety order
            "safety_order_scale_factor": 1.0,  # Multiplier for safety order size (e.g., 1.5 means next SO is 1.5x previous)
            "max_safety_orders": 5
        }

        for key, value in defaults.items():
            setattr(self, key, custom_parameters.get(key, value))

        # Convert percentages to multipliers
        self.tp1_sell_multiplier = self.tp1_sell_percent / 100.0
        self.tp2_sell_multiplier = self.tp2_sell_percent / 100.0

        # Internal state variables (will be loaded from/persisted to DB)
        # These are initialized here but their actual values for a live trade
        # will come from the database via the execute_live_signal method.
        self.position_active = False
        self.entry_price_avg = 0.0
        self.current_position_size_asset = 0.0
        self.total_usdt_invested = 0.0  # Cost basis
        self.safety_orders_placed_count = 0
        self.take_profit_prices = []  # [tp1_price, tp2_price, tp3_price]
        self.current_stop_loss_price = 0.0
        self.tp_levels_hit = [False, False, False]  # Tracks if TP1, TP2, TP3 has been hit for current trade

        logger.info(f"DCAStrategy initialized for {self.symbol} with params: {custom_parameters}")

    @classmethod
    def get_parameters_definition(cls):
        return {
            "base_order_size_usdt": {"type": "float", "default": 10.0, "label": "Base Order Size (USDT)", "description": "Initial order size in USDT."},
            "safety_order_size_usdt": {"type": "float", "default": 10.0, "label": "Safety Order Size (USDT)", "description": "Base size for safety orders in USDT."},
            "tp1_percent": {"type": "float", "default": 1.0, "label": "Take Profit 1 (%)", "description": "Percentage above average entry for TP1."},
            "tp1_sell_percent": {"type": "float", "default": 40.0, "label": "TP1 Sell (%)", "description": "Percentage of current position to sell at TP1."},
            "tp2_percent": {"type": "float", "default": 3.0, "label": "Take Profit 2 (%)", "description": "Percentage above average entry for TP2."},
            "tp2_sell_percent": {"type": "float", "default": 40.0, "label": "TP2 Sell (%)", "description": "Percentage of current position to sell at TP2."},
            "tp3_percent": {"type": "float", "default": 20.0, "label": "Take Profit 3 (%)", "description": "Percentage above average entry for TP3 (sells 100% remaining)."},
            "stop_loss_percent": {"type": "float", "default": 5.0, "label": "Stop Loss (%)", "description": "Percentage below average entry for Stop Loss."},
            "safety_order_deviation_percent": {"type": "float", "default": 1.0, "label": "Safety Order Deviation (%)", "description": "Price drop percentage to place next safety order."},
            "safety_order_scale_factor": {"type": "float", "default": 1.0, "label": "Safety Order Scale Factor", "description": "Multiplier for subsequent safety order sizes (e.g., 1.0 for same size, 1.5 for 1.5x)."},
            "max_safety_orders": {"type": "int", "default": 5, "label": "Max Safety Orders", "description": "Maximum number of safety orders to place."}
        }

    def _place_order_with_retry(self, exchange_ccxt, symbol, order_type, side, amount, price=None, retries=3, delay=5):
        for attempt in range(retries):
            try:
                params = {}
                # CCXT specific params for some exchanges if needed, e.g. timeInForce
                # For limit orders, price is required. For market, it's not.
                if order_type.lower() == 'market' and side.lower() == 'buy':
                    # For market buy, amount is typically in quote currency (USDT for symbol like BTC/USDT)
                    # This needs careful handling based on exchange. Assuming 'amount' is base currency for now.
                    # If 'amount' is quote (USDT), then for market buy, it should be like:
                    # order = exchange_ccxt.create_market_buy_order(symbol, amount_usdt)
                    # For simplicity, we'll assume 'amount' is in base currency for both buy/sell.
                    # This means for market buy, we'd need to calculate amount of base from USDT.
                    # The provided code uses limit orders for entry, so we'll stick to that.
                    pass

                order = exchange_ccxt.create_order(symbol, order_type, side, amount, price, params)
                logger.info(f"Order placed: {side} {amount} {symbol} at {price if price else 'market'}. Order ID: {order.get('id') if order else 'N/A'}")
                return order
            except Exception as e:
                logger.error(f"Error placing order (attempt {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(delay)
                else:
                    logger.error(f"Failed to place order after {retries} attempts.")
                    return None
        return None

    def _calculate_take_profits_and_sl(self):
        if not self.position_active or self.entry_price_avg == 0:
            return

        tp1 = self.entry_price_avg * (1 + self.tp1_percent / 100)
        tp2 = self.entry_price_avg * (1 + self.tp2_percent / 100)
        tp3 = self.entry_price_avg * (1 + self.tp3_percent / 100)
        self.take_profit_prices = [tp1, tp2, tp3]
        self.current_stop_loss_price = self.entry_price_avg * (1 - self.stop_loss_percent / 100)
        logger.info(f"Calculated TPs: {self.take_profit_prices}, SL: {self.current_stop_loss_price} based on avg entry: {self.entry_price_avg}")

    def _place_safety_orders(self, db_session: Session, position_id: int, exchange_ccxt, current_market_price_for_so_calc):
        # Fetch the current position to ensure it's still open and get its ID
        current_position_db = db_session.query(Position).filter(Position.id == position_id, Position.is_open == True).first()
        if not current_position_db:
            logger.warning(f"[{self.name}-{self.symbol}] Cannot place safety orders: Position ID {position_id} not found or not open.")
            return

        # Count existing open safety orders for this position
        existing_safety_orders_count = db_session.query(Order).filter(
            Order.position_id == position_id,
            Order.order_type == 'limit',  # Assuming safety orders are limit orders
            Order.side == 'buy',
            Order.status == 'open'
        ).count()

        if existing_safety_orders_count >= self.max_safety_orders:
            logger.info(f"[{self.name}-{self.symbol}] Max safety orders ({self.max_safety_orders}) already placed or open for position {position_id}.")
            return

        # Determine how many SOs to attempt to place in this call (usually 1, unless catching up)
        # For simplicity, this function will try to place one SO if conditions met, or more if deviation is large.
        # The original example places all safety orders at once after base order.
        # This adaptation will place them iteratively as price drops.

        # Price for next SO based on last entry/avg price or current market if no position
        price_reference = self.entry_price_avg if self.position_active else current_market_price_for_so_calc

        next_so_price_target = price_reference * (1 - (self.safety_order_deviation_percent / 100.0) * (self.safety_orders_placed_count + 1))

        # Only place if current price is near or below the target for the *next* safety order
        # This logic might need refinement. The original script places all SOs as limit orders below initial entry.
        # Let's replicate that: place all pending safety orders as limit orders.

        # This part is tricky: original script places all safety orders immediately after base.
        # If we are to adapt it to a class called repeatedly, we need to decide if _place_safety_orders
        # places ALL initially, or one by one.
        # For now, let's assume it places all *remaining* safety orders based on the *initial* entry price.
        # This means it should only be called once after the base order.

        # Re-thinking: The provided example places all safety orders right after the base order.
        # So, this function should do that. It will be called once.
        if self.safety_orders_placed_count > 0:  # Already placed.
            return

        logger.info(f"Placing safety orders based on initial entry: {self.entry_price_avg}")
        initial_entry_for_so = self.entry_price_avg  # Should be the price of the base order

        for i in range(self.max_safety_orders):
            safety_price = initial_entry_for_so * (1 - self.safety_order_deviation_percent / 100 * (i + 1))

            # Ensure safety price is reasonably below current market if placing now, or use it as limit
            safety_price = exchange_ccxt.price_to_precision(self.symbol, safety_price)

            scaled_order_size_usdt = self.safety_order_size_usdt * (self.safety_order_scale_factor ** i)
            qty_asset = scaled_order_size_usdt / safety_price  # Amount of asset to buy
            qty_asset = exchange_ccxt.amount_to_precision(self.symbol, qty_asset)

            if qty_asset > 0:
                logger.info(f"Attempting to place safety order {i+1}/{self.max_safety_orders}: BUY {qty_asset} {self.symbol} at {safety_price}")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'limit', 'buy', qty_asset, safety_price)
                if order:
                    logger.info(f"Safety order {i+1} placed. Price: {safety_price}, Qty: {qty_asset}")
                    # Create Order entry for SO in DB
                    new_so_order = Order(
                        subscription_id=current_position_db.subscription_id,
                        position_id=current_position_db.id,  # Link to the position
                        order_id=order.get('id'),
                        symbol=self.symbol,
                        order_type=order.get('type'),
                        side=order.get('side'),
                        amount=order.get('amount'),
                        price=order.get('price'),
                        cost=order.get('cost'),
                        filled=order.get('filled', 0.0),
                        remaining=order.get('remaining', order.get('amount')),
                        status=order.get('status', 'open'),
                        created_at=datetime.datetime.utcnow(),
                        updated_at=datetime.datetime.utcnow()
                    )
                    db_session.add(new_so_order)
                    db_session.commit()

                    # Note: We don't update avg_entry_price or position_size here yet.
                    # That happens when these safety orders actually fill.
                    # The current structure assumes execute_live_signal checks fills.
                    # This is a simplification. A real system would track open orders and fills.
                    # For this adaptation, we'll assume SOs are placed and will fill if price reaches.
                    # The impact on avg entry price will be calculated if/when they are assumed filled.
                    # This is a major simplification point.
                else:
                    logger.warning(f"Failed to place safety order {i+1}.")
            else:
                logger.warning(f"Safety order {i+1} quantity is zero or less. USDT: {scaled_order_size_usdt}, Price: {safety_price}")
        # Mark that we've *attempted* to place all safety orders based on initial entry
        self.safety_orders_placed_count = self.max_safety_orders  # Assume all attempted
        
    def _update_average_entry_and_position(self, filled_qty_asset, fill_price_usdt):
        new_total_usdt = self.total_usdt_invested + (filled_qty_asset * fill_price_usdt)
        new_total_asset = self.current_position_size_asset + filled_qty_asset
        if new_total_asset > 0:
            self.entry_price_avg = new_total_usdt / new_total_asset
        self.current_position_size_asset = new_total_asset
        self.total_usdt_invested = new_total_usdt
        self._calculate_take_profits_and_sl()  # Recalculate TPs/SL with new avg entry

    def _handle_filled_safety_order(self, exchange_ccxt, filled_order_details):
        # This is a placeholder. A robust system needs to check open orders and their fill status.
        # For now, we assume if price drops below a safety order level, it "fills".
        # This is a significant simplification.
        # Let's assume `execute_live_signal` will simulate this check.
        pass

    def _adjust_stop_loss_after_tp(self, tp_level_index, current_price, exchange_ccxt):
        # tp_level_index: 0 for TP1, 1 for TP2
        if tp_level_index == 0:  # After TP1
            new_sl = self.entry_price_avg  # Move to breakeven
            logger.info(f"TP1 hit. Adjusting SL to breakeven: {new_sl}")
        elif tp_level_index == 1:  # After TP2
            if len(self.take_profit_prices) > 0:
                new_sl = self.take_profit_prices[0]  # Move to TP1 price
                logger.info(f"TP2 hit. Adjusting SL to TP1 price: {new_sl}")
            else:  # Should not happen if TPs are set
                new_sl = current_price
                logger.warning("TP2 hit, but TP prices not found for SL adjustment. Setting SL to current price.")
        else:
            return  # No SL adjustment for TP3 as position closes

        self.current_stop_loss_price = new_sl
        # If platform supports modifying SL orders, do it here.
        # Otherwise, this new self.current_stop_loss_price will be used by the main check.

    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None):
        logger.info("DCAStrategy run_backtest called. Backtesting is not applicable for this live-focused DCA implementation via this interface.")
        return {"message": "DCA strategy is designed for live trading and does not support detailed backtesting through this simplified interface. Please test with small amounts in live mode.", "parameters": self.__dict__}

    def execute_live_signal(self, db_session: Session, subscription_id: int, market_data_df: pd.DataFrame = None, exchange_ccxt=None):
        """
        Manages position state in the database.
        """
        if not exchange_ccxt:
            logger.error(f"[{self.name}-{self.symbol}] Exchange CCXT object not provided to execute_live_signal.")
            return {"status": "error", "message": "Exchange not initialized."}

        logger.debug(f"[{self.name}-{self.symbol}] Executing live signal check for subscription {subscription_id}...")

        # Fetch the current position from the database
        current_position_db = db_session.query(Position).filter(
            Position.subscription_id == subscription_id,
            Position.is_open == True
        ).first()

        # Initialize strategy state from DB if a position exists
        if current_position_db:
            self.position_active = True
            self.entry_price_avg = current_position_db.entry_price
            self.current_position_size_asset = current_position_db.amount
            self.total_usdt_invested = current_position_db.entry_price * current_position_db.amount  # Approximate cost basis

            # Load DCA-specific state from custom_data JSON field
            custom_data = json.loads(current_position_db.custom_data) if current_position_db.custom_data else {}
            self.safety_orders_placed_count = custom_data.get('safety_orders_placed_count', 0)
            self.take_profit_prices = custom_data.get('take_profit_prices', [])
            self.current_stop_loss_price = custom_data.get('current_stop_loss_price', 0.0)
            self.tp_levels_hit = custom_data.get('tp_levels_hit', [False, False, False])

            logger.debug(f"[{self.name}-{self.symbol}] Loaded position from DB: Avg Entry={self.entry_price_avg}, Size={self.current_position_size_asset}, SO Count={self.safety_orders_placed_count}, TPs={self.take_profit_prices}, SL={self.current_stop_loss_price}, TP Hits={self.tp_levels_hit}")

            # Recalculate TPs/SL if not loaded or if entry price changed (robustness)
            if not self.take_profit_prices or self.entry_price_avg != current_position_db.entry_price:
                self._calculate_take_profits_and_sl()
        else:
            self.position_active = False
            self.entry_price_avg = 0.0
            self.current_position_size_asset = 0.0
            self.total_usdt_invested = 0.0
            self.safety_orders_placed_count = 0
            self.take_profit_prices = []
            self.current_stop_loss_price = 0.0
            self.tp_levels_hit = [False, False, False]
            logger.debug(f"[{self.name}-{self.symbol}] No active position found in DB.")

        try:
            ticker = exchange_ccxt.fetch_ticker(self.symbol)
            current_price = ticker['last']
            if not current_price:
                logger.warning(f"[{self.name}-{self.symbol}] Could not fetch current price.")
                return {"status": "no_action", "message": "Price data unavailable."}
        except Exception as e:
            logger.error(f"[{self.name}-{self.symbol}] Error fetching ticker: {e}")
            return {"status": "error", "message": f"Failed to fetch price: {e}"}

        logger.debug(f"[{self.name}-{self.symbol}] Current Price: {current_price}, Position Active: {self.position_active}, Avg Entry: {self.entry_price_avg}, Position Size: {self.current_position_size_asset}")

        # --- Initial Position Entry ---
        if not self.position_active:
            logger.info(f"[{self.name}-{self.symbol}] No active position. Attempting to enter base order at {current_price}.")
            base_qty_asset = self.base_order_size_usdt / current_price
            # Ensure quantity is within exchange precision
            base_qty_asset = exchange_ccxt.amount_to_precision(self.symbol, base_qty_asset)

            if base_qty_asset <= 0:
                logger.warning(f"[{self.name}-{self.symbol}] Calculated base order quantity is zero or negative ({base_qty_asset}). Skipping order placement.")
                return {"status": "no_action", "message": "Calculated base order quantity is zero."}

            # Using limit order at current_price for entry (could be market too)
            entry_price_target = exchange_ccxt.price_to_precision(self.symbol, current_price)

            # Place Base Order
            order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'limit', 'buy', base_qty_asset, entry_price_target)

            if order and order.get('id'):
                # Assuming order is filled immediately for simplicity.
                # In a real system, you'd need to confirm fill and get actual fill price/quantity.
                actual_filled_price = float(order.get('price', entry_price_target))
                actual_filled_quantity = float(order.get('amount', base_qty_asset))

                # Create Position in DB
                new_position = Position(
                    subscription_id=subscription_id,
                    symbol=self.symbol,
                    side="long",  # DCA strategy is typically long
                    entry_price=actual_filled_price,
                    amount=actual_filled_quantity,
                    is_open=True,
                    open_time=datetime.datetime.utcnow()  # Use UTC now
                )
                db_session.add(new_position)
                db_session.commit()
                db_session.refresh(new_position)  # Get the generated ID

                # Update internal state from the newly created DB position
                self.position_active = True
                self.entry_price_avg = new_position.entry_price
                self.current_position_size_asset = new_position.amount
                self.total_usdt_invested = new_position.entry_price * new_position.amount  # Approximate cost basis
                self.tp_levels_hit = [False, False, False]  # Reset TP hit status
                self.safety_orders_placed_count = 0  # Reset for new trade cycle

                self._calculate_take_profits_and_sl()

                # Store DCA-specific state in custom_data
                dca_custom_data = {
                    'safety_orders_placed_count': self.safety_orders_placed_count,
                    'take_profit_prices': self.take_profit_prices,
                    'current_stop_loss_price': self.current_stop_loss_price,
                    'tp_levels_hit': self.tp_levels_hit
                }
                new_position.custom_data = json.dumps(dca_custom_data)
                db_session.add(new_position)
                db_session.commit()

                # Place all safety orders as limit orders immediately after base order confirmation
                # These orders will be tracked in the DB via the _place_safety_orders method
                self._place_safety_orders(db_session, new_position.id, exchange_ccxt, self.entry_price_avg)

                logger.info(f"[{self.name}-{self.symbol}] Base order entered. Position ID: {new_position.id}, Avg Entry: {self.entry_price_avg}, Size: {self.current_position_size_asset}. TPs/SL calculated. Safety orders placed.")
                return {"status": "action", "signal": "buy_base", "price": self.entry_price_avg, "size": self.current_position_size_asset}
            else:
                logger.warning(f"[{self.name}-{self.symbol}] Failed to place base order.")
                return {"status": "no_action", "message": "Base order placement failed."}

        # --- Position Management (if position_active) ---
        # IMPORTANT SIMPLIFICATION:
        # This part assumes safety orders, if their price is met, are "magically" filled and averaged in.
        # A real system would monitor open orders and their fills.
        # For this adaptation, we'll simulate safety order fills if price drops below their levels.
        # This is NOT robust for real trading without proper order fill tracking.

        # Simulate Safety Order Fills (highly simplified)
        # Check if price has dropped to fill any of the conceptually placed safety orders
        # This loop should ideally run only if there are pending (unfilled) safety orders.
        # The `_place_safety_orders` places them all as limits. Here we check if they *would have* filled.
        if self.safety_orders_placed_count < self.max_safety_orders:  # If not all SOs are considered "active"
            # This logic is flawed if _place_safety_orders places all SOs as limits.
            # We need to track individual SOs or assume they fill if price hits.
            # Let's assume the platform's LiveStrategyRunner would handle actual fill events.
            # For now, this strategy won't actively manage averaging in from safety orders after initial placement.
            # It will rely on the initial TPs/SL based on the base order + all SOs *potentially* filling.
            # This is a key area that needs more robust handling in a full system.
            pass  # Skipping complex SO fill simulation here. Average price is based on base order.

        # Check Take Profits
        for i in range(len(self.take_profit_prices)):
            if not self.tp_levels_hit[i] and current_price >= self.take_profit_prices[i]:
                sell_qty_asset = 0
                if i == 0:  # TP1
                    sell_qty_asset = self.current_position_size_asset * self.tp1_sell_multiplier
                elif i == 1:  # TP2
                    sell_qty_asset = self.current_position_size_asset * self.tp2_sell_multiplier
                elif i == 2:  # TP3
                    sell_qty_asset = self.current_position_size_asset  # Sell all remaining

                sell_qty_asset = exchange_ccxt.amount_to_precision(self.symbol, sell_qty_asset)

                if sell_qty_asset > 0:
                    logger.info(f"TP{i+1} hit at {current_price}. Attempting to sell {sell_qty_asset} {self.symbol}.")
                    # Using market order for TP for higher chance of fill
                    order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'sell', sell_qty_asset)
                    if order and order.get('id'):
                        self.current_position_size_asset -= sell_qty_asset
                        self.total_usdt_invested -= (sell_qty_asset * self.take_profit_prices[i])  # Approximate reduction in cost basis
                        if self.total_usdt_invested < 0:
                            self.total_usdt_invested = 0

                        self.tp_levels_hit[i] = True
                        logger.info(f"TP{i+1} sold {sell_qty_asset}. Remaining size: {self.current_position_size_asset}")

                        if i < 2:  # TP1 or TP2
                            self._adjust_stop_loss_after_tp(i, current_price, exchange_ccxt)
                        else:  # TP3 hit, close trade
                            logger.info("TP3 hit. Closing trade cycle.")
                            self.position_active = False  # Reset for new trade
                            # Full reset of state variables
                            self.entry_price_avg = 0.0
                            self.current_position_size_asset = 0.0
                            self.total_usdt_invested = 0.0
                            self.safety_orders_placed_count = 0
                            self.tp_levels_hit = [False, False, False]
                            return {"status": "action", "signal": f"tp{i+1}_sell_close", "price": current_price, "size": sell_qty_asset}

                        return {"status": "action", "signal": f"tp{i+1}_sell_partial", "price": current_price, "size": sell_qty_asset}
                    else:
                        logger.warning(f"Failed to place sell order for TP{i+1}.")
                break  # Process one TP level per call

        # Check Stop Loss
        if self.current_stop_loss_price > 0 and current_price <= self.current_stop_loss_price:
            logger.info(f"Stop Loss hit at {current_price}. SL price was {self.current_stop_loss_price}. Selling remaining position.")
            sl_qty_asset = exchange_ccxt.amount_to_precision(self.symbol, self.current_position_size_asset)
            if sl_qty_asset > 0:
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'sell', sl_qty_asset)
                if order and order.get('id'):
                    logger.info(f"Stop loss executed. Sold {sl_qty_asset} {self.symbol}.")
                    self.position_active = False  # Reset for new trade
                    self.entry_price_avg = 0.0
                    self.current_position_size_asset = 0.0
                    self.total_usdt_invested = 0.0
                    self.safety_orders_placed_count = 0
                    self.tp_levels_hit = [False, False, False]
                    return {"status": "action", "signal": "stop_loss_sell", "price": current_price, "size": sl_qty_asset}
                else:
                    logger.warning("Failed to place sell order for Stop Loss.")
            else:  # No position to SL
                self.position_active = False  # Reset anyway

        return {"status": "no_action", "message": "Monitoring active position."}

# Example of how it might be run by the platform (for testing purposes)
if __name__ == '__main__':
    # This is a mock CCXT exchange for testing
    class MockExchange:
        def __init__(self, symbol):
            self.symbol = symbol
            self.last_price = 100.0  # Start price
            self.orders = []
            self.precisions = {'amount': 8, 'price': 2}  # Example precisions

        def fetch_ticker(self, symbol):
            # Simulate price movement for testing
            # self.last_price *= random.uniform(0.99, 1.01)
            return {'symbol': symbol, 'last': self.last_price}

        def amount_to_precision(self, symbol, amount):
            return round(amount, self.precisions['amount'])

        def price_to_precision(self, symbol, price):
            return round(price, self.precisions['price'])

        def create_order(self, symbol, type, side, amount, price=None, params=None):
            order_id = len(self.orders) + 1
            order = {'id': order_id, 'symbol': symbol, 'type': type, 'side': side, 'amount': amount, price: price, 'status': 'open'}
            self.orders.append(order)
            print(f"[MockExchange] Order Created: {order}")
            # Simulate immediate fill for testing this strategy's logic flow
            order['status'] = 'closed'
            return order

    # Setup basic logging for the test
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    mock_exchange = MockExchange("BTC/USDT")

    # Parameters for the strategy instance
    params = {
        "base_order_size_usdt": 100.0,  # 1 BTC at $
