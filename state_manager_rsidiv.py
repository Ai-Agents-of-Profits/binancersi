import json
import os
import logging
import portalocker

DEFAULT_STATE = {
    "active_trade": False,
    "position_side": None,
    "entry_price": None,
    "stop_loss_price": None,  # Initial stop loss
    "target_price": None,
    "highest": None,
    "lowest": None,
    "trailing_stop_level": None,
    "sl_order_id": None,
    "tp_order_id": None,
    "ts_order_id": None
}
STATE_FILE = 'state_rsidiv.json'

def get_state_file_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, STATE_FILE)

def initialize_state():
    state_file = get_state_file_path()
    if not os.path.exists(state_file):
        logging.info(f"State file not found. Creating {state_file} with default state.")
        try:
            with open(state_file, 'w') as f:
                json.dump(DEFAULT_STATE, f, indent=4)
        except Exception as e:
            logging.error(f"Error creating state file {state_file}: {e}")

def get_state():
    state_file = get_state_file_path()
    initialize_state()
    try:
        with open(state_file, 'r') as f:
            portalocker.lock(f, portalocker.LOCK_SH)
            state = json.load(f)
            portalocker.unlock(f)
            # Validate state keys
            for k in DEFAULT_STATE:
                if k not in state:
                    state[k] = DEFAULT_STATE[k]
            return state
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {state_file}. Using default state.")
        return DEFAULT_STATE.copy()
    except Exception as e:
        logging.error(f"Error loading state from {state_file}: {e}. Using default state.")
        return DEFAULT_STATE.copy()

def set_state(new_state):
    state_file = get_state_file_path()
    # Ensure all keys are present
    for k in DEFAULT_STATE:
        if k not in new_state:
            new_state[k] = DEFAULT_STATE[k]
    try:
        with open(state_file, 'w') as f:
            portalocker.lock(f, portalocker.LOCK_EX)
            json.dump(new_state, f, indent=4)
            portalocker.unlock(f)
    except Exception as e:
        logging.error(f"Error saving state to {state_file}: {e}")

def reset_state():
    logging.info("Resetting bot state.")
    set_state(DEFAULT_STATE.copy())
