import ccxt
import os
import time
import schedule
import logging
import pandas as pd
from dotenv import load_dotenv
from datetime import datetime
from colorama import Fore, Style
import colorama

# --- Local Modules ---
from state_manager_rsidiv import initialize_state, get_state, set_state, reset_state
from functions_rsidiv import fetch_candles, compute_indicators, detect_rsi_divergence

# --- Setup ---
colorama.init(autoreset=True)
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Configuration ---
SYMBOL = 'BERA/USDT' # Binance symbol format
TIMEFRAME = '1h'
ORDER_SIZE_USD = 25  # Match your backtest
FETCH_LIMIT = 200
SCHEDULE_INTERVAL_SECONDS = 60
USE_TESTNET = False

# Strategy Parameters (Best Backtest)
RSI_LENGTH = 6
ATR_LENGTH = 6
ATR_MULTIPLIER = 1.1
PROFIT_TARGET_PCT = 0.01
STOP_LOSS_PCT = 0.008
SWING_WINDOW = 4

# --- Exchange Setup ---
logging.info("Connecting to Binance Futures...")
try:
    # Set longer timeout for requests to prevent timeouts
    exchange_config = {
        'enableRateLimit': True,
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_API_SECRET'),
        'options': {
            'defaultType': 'future',
            'timeout': 30000,  # Timeout in milliseconds (30 seconds)
        }
    }
    # Check if we should use the proxy based on environment variable
    USE_PROXY = os.getenv('USE_PROXY', 'false').lower() == 'true'
    if USE_PROXY:
        logging.info("Using Fixie proxy for connection")
        exchange_config['proxies'] = {
            'http': 'http://fixie:jqqXTVRSClx3W68@ventoux.usefixie.com:80',
            'https': 'http://fixie:jqqXTVRSClx3W68@ventoux.usefixie.com:80'
        }
    else:
        logging.info("Connecting directly without proxy")
    exchange = ccxt.binance(exchange_config)
    if not os.getenv('BINANCE_API_KEY') or not os.getenv('BINANCE_API_SECRET'):
        raise ValueError("API Key/Secret missing in .env")
    if USE_TESTNET:
        logging.info("Using Binance Futures Testnet")
        exchange.set_sandbox_mode(True)
    else:
        logging.info("Using Binance Futures Mainnet")
    exchange.load_markets()
    market = exchange.market(SYMBOL)
    AMOUNT_PRECISION = market['precision']['amount']
    PRICE_PRECISION = market['precision']['price']
    logging.info(f"Connected to Binance Futures. Amount precision: {AMOUNT_PRECISION}, Price precision: {PRICE_PRECISION}")
except Exception as e:
    logging.critical(f"Exchange setup failed: {e}", exc_info=True)
    exit()

def step_to_decimals(step):
    if step >= 1:
        return 0
    s = str(step)
    if '.' in s:
        return len(s.split('.')[1].rstrip('0'))
    return 0

# --- State ---
initialize_state()

def print_header():
    print(f"\n{Fore.CYAN}{'=' * 80}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}{'RSI DIVERGENCE STRATEGY BOT':^80}")
    print(f"{Fore.CYAN}{'-' * 80}")
    print(f"{Fore.GREEN}Symbol: {Fore.WHITE}{SYMBOL.replace('/', '')} | {Fore.GREEN}Timeframe: {Fore.WHITE}{TIMEFRAME} | {Fore.GREEN}Order Size: {Fore.WHITE}${ORDER_SIZE_USD} USD")
    print(f"{Fore.GREEN}RSI: {Fore.WHITE}{RSI_LENGTH} | ATR: {Fore.WHITE}{ATR_LENGTH} | ATR Mult: {Fore.WHITE}{ATR_MULTIPLIER} | PT: {Fore.WHITE}{PROFIT_TARGET_PCT*100:.2f}% | SL: {Fore.WHITE}{STOP_LOSS_PCT*100:.2f}% | SWING: {Fore.WHITE}{SWING_WINDOW}")
    print(f"{Fore.CYAN}{'=' * 80}\n{Style.RESET_ALL}")

print_header()

# --- Main Bot Logic ---
def bot_logic():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n{Fore.CYAN}[{now}] {Style.BRIGHT}Running RSI Divergence Cycle [{TIMEFRAME}] {Style.RESET_ALL}")
    logging.info(f"--- Running RSI Divergence Cycle [{TIMEFRAME}] ---")
    state = get_state()
    try:
        # --- Sync with Exchange --- #
        positions = exchange.fetch_positions(symbols=[SYMBOL])
        # Robust symbol matching for Binance Futures
        exch_pos = next(
            (p for p in positions if p['symbol'] == SYMBOL or p['symbol'].replace(':USDT', '') == SYMBOL or SYMBOL in p['symbol']),
            None
        )
        exch_size_str = exch_pos['info'].get('positionAmt', '0') if exch_pos else '0'
        exch_size = float(exch_size_str) # Can be negative for short
        exch_in_pos = abs(exch_size) > 0
        is_long = exch_size > 0
        # State reconciliation
        if state.get('active_trade', False) and not exch_in_pos:
            logging.warning("Bot active but no exchange position found. Resetting state.")
            reset_state()
            state = get_state()
        elif not state.get('active_trade', False) and exch_in_pos:
            logging.error("Exchange position found, but bot inactive. Manual intervention needed. Bot exiting cycle.")
            return
        # --- Get Data & Indicators ---
        df = fetch_candles(exchange, SYMBOL, TIMEFRAME, FETCH_LIMIT)
        if df.empty or len(df) < FETCH_LIMIT:
            logging.warning(f"Insufficient candle data ({len(df)}). Skipping.")
            return
        df = compute_indicators(df, rsi_length=RSI_LENGTH, atr_length=ATR_LENGTH)
        df = detect_rsi_divergence(df, swing_window=SWING_WINDOW, align_window=3)
        df.dropna(inplace=True)
        latest = df.iloc[-1]
        price = latest['close']
        atr_val = latest['ATR']
        # --- EXIT LOGIC --- #
        if state.get('active_trade', False):
            stop_loss_price = state.get('stop_loss_price')
            target_price = state.get('target_price')
            entry_price = state.get('entry_price')
            is_long = state['position_side'] == 'long'
            close_reason = None
            # --- Trailing Stop Logic ---
            highest = state.get('highest')
            lowest = state.get('lowest')
            trailing_stop_level = state.get('trailing_stop_level')
            trailing_stop_updated = False
            if is_long:
                # Update highest price since entry
                if highest is None or price > highest:
                    highest = price
                trail_dist = max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                new_trailing_stop = highest - trail_dist
                # Only move trailing stop up
                if trailing_stop_level is None or new_trailing_stop > trailing_stop_level:
                    logging.info(f"Trailing stop updated (long): {trailing_stop_level} -> {new_trailing_stop} (highest: {highest}, trail_dist: {trail_dist})")
                    trailing_stop_level = new_trailing_stop
                    trailing_stop_updated = True
                if price <= trailing_stop_level:
                    close_reason = f"TRAILING STOP LOSS Hit! Price={price:.4f}, TSL={trailing_stop_level:.4f}"
                elif price >= target_price:
                    close_reason = f"PROFIT TARGET Hit! Price={price:.4f}, TP={target_price:.4f}"
            else:
                if lowest is None or price < lowest:
                    lowest = price
                trail_dist = max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                new_trailing_stop = lowest + trail_dist
                # Only move trailing stop down
                if trailing_stop_level is None or new_trailing_stop < trailing_stop_level:
                    logging.info(f"Trailing stop updated (short): {trailing_stop_level} -> {new_trailing_stop} (lowest: {lowest}, trail_dist: {trail_dist})")
                    trailing_stop_level = new_trailing_stop
                    trailing_stop_updated = True
                if price >= trailing_stop_level:
                    close_reason = f"TRAILING STOP LOSS Hit! Price={price:.4f}, TSL={trailing_stop_level:.4f}"
                elif price <= target_price:
                    close_reason = f"PROFIT TARGET Hit! Price={price:.4f}, TP={target_price:.4f}"
            if close_reason:
                print(f"\n{Fore.RED}{Style.BRIGHT}EXIT SIGNAL: {close_reason}. Closing {state['position_side']} position.{Style.RESET_ALL}")
                logging.info(f"EXIT SIGNAL: {close_reason}. Closing {state['position_side']} position.")
                # Market close
                side = 'sell' if is_long else 'buy'
                try:
                    params = {'reduceOnly': True}
                    order = exchange.create_market_order(SYMBOL, side, abs(exch_size), params=params)
                    logging.info(f"Market close order placed: {order.get('id', 'N/A')}")
                    reset_state()
                    print(f"{Fore.MAGENTA}Position closed successfully. State reset.{Style.RESET_ALL}")
                except Exception as e:
                    logging.error(f"Market close FAILED: {e}")
                return
            else:
                # Save trailing stop/highest/lowest to state
                state['stop_loss_price'] = stop_loss_price
                state['highest'] = highest
                state['lowest'] = lowest
                state['trailing_stop_level'] = trailing_stop_level
                set_state(state)
                profit_pct = ((price / entry_price - 1) * 100) if is_long else ((entry_price / price - 1) * 100)
                profit_color = Fore.GREEN if profit_pct > 0 else Fore.RED
                print(f"{Fore.CYAN}Active {Fore.GREEN if is_long else Fore.RED}{state['position_side'].upper()} position: Entry={entry_price:.4f}, Current={price:.4f}, SL={stop_loss_price:.4f}, TP={target_price:.4f}, TSL={trailing_stop_level:.4f}, P/L: {profit_color}{profit_pct:.2f}%")
                logging.info("Holding position. No exit signal.")
        # --- ENTRY LOGIC --- #
        elif not state.get('active_trade', False):
            if latest['bullish_div']:
                side = 'buy'
                pos_side = 'long'
            elif latest['bearish_div']:
                side = 'sell'
                pos_side = 'short'
            else:
                print(f"{Fore.YELLOW}No entry conditions met.{Style.RESET_ALL}")
                logging.info("No entry conditions met.")
                return
            try:
                amount = ORDER_SIZE_USD / price
                amount_decimals = step_to_decimals(AMOUNT_PRECISION)
                amount_str = f"{amount:.{amount_decimals}f}"
                amount_float = float(amount_str)
                if amount_float <= 0:
                    logging.error(f"Calculated amount {amount_float} invalid. Skipping entry.")
                    return
                logging.info(f"Attempting {side.upper()} entry: {amount_str} {SYMBOL.split('/')[0]} @ Market")
                params = {} # No special params needed for Binance entry
                order = exchange.create_market_order(SYMBOL, side, amount_float, params=params)
                logging.info(f"Entry order placed: {order.get('id', 'N/A')}")
                print(f"{Fore.GREEN}Entry order placed: {order.get('id', 'N/A')}")
                # Set stop loss and target
                if side == 'buy':
                    stop_loss_price = price - max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                    target_price = price + price * PROFIT_TARGET_PCT
                    highest = price
                    lowest = None
                    trailing_stop_level = highest - max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                else:
                    stop_loss_price = price + max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                    target_price = price - price * PROFIT_TARGET_PCT
                    highest = None
                    lowest = price
                    trailing_stop_level = lowest + max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                price_decimals = step_to_decimals(PRICE_PRECISION)
                stop_loss_price = float(f"{stop_loss_price:.{price_decimals}f}")
                target_price = float(f"{target_price:.{price_decimals}f}")
                trailing_stop_level = float(f"{trailing_stop_level:.{price_decimals}f}")
                new_state = {
                    "active_trade": True,
                    "position_side": pos_side,
                    "entry_price": price,
                    "stop_loss_price": stop_loss_price,
                    "target_price": target_price,
                    "highest": highest,
                    "lowest": lowest,
                    "trailing_stop_level": trailing_stop_level
                }
                set_state(new_state)
                print(f"{Fore.YELLOW}Stop loss set at: {stop_loss_price:.4f}, Target: {target_price:.4f}, Initial Trailing Stop: {trailing_stop_level:.4f}")
                logging.info(f"State updated: {new_state}")
                time.sleep(5)
            except ccxt.InsufficientFunds as e:
                logging.error(f"Insufficient funds for entry: {e}")
                print(f"{Fore.RED}{Style.BRIGHT}Insufficient fundss for entry: {e}{Style.RESET_ALL}")
            except Exception as e:
                logging.error(f"Entry error: {e}", exc_info=True)
                print(f"{Fore.RED}{Style.BRIGHT}Entry error: {e}{Style.RESET_ALL}")
    except Exception as e:
        logging.error(f"Unexpected Error in bot_logic: {e}", exc_info=True)
        print(f"{Fore.RED}{Style.BRIGHT}Unexpected Error in bot_logic: {e}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[{datetime.now().strftime('%H:%M:%S')}] Cycle completed {Style.RESET_ALL}")
    logging.info(f"--- RSI Divergence Cycle End ---\n")

print(f"\n{Fore.GREEN}{Style.BRIGHT}Starting RSI Divergence Bot for Binance Futures{Style.RESET_ALL}")
print(f"{Fore.CYAN}Checking conditions every {SCHEDULE_INTERVAL_SECONDS} seconds. Press Ctrl+C to stop.{Style.RESET_ALL}\n")
logging.info("Starting RSI Divergence Bot for Binance Futures")
schedule.every(SCHEDULE_INTERVAL_SECONDS).seconds.do(bot_logic)
bot_logic()
while True:
    try:
        schedule.run_pending()
        time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
        break
    except Exception as e:
        logging.critical(f"MAIN LOOP ERROR: {e}", exc_info=True)
        logging.info("Sleeping 60s...")
        time.sleep(60)
logging.info("Bot finished.")
