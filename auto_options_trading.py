import os
import time
import csv
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("UPSTOX_ANALYTICS_TOKEN")

BASE_URL = "https://api.upstox.com/v2"

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {TOKEN}"
}


# ============================================================
# CONFIGURATION
# ============================================================

UNDERLYINGS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "SENSEX": "BSE_INDEX|SENSEX"
}

EXPIRY = "current_week"

PAPER_TRADING = True

MIN_CONFIDENCE = 70

STOP_LOSS_PERCENT = 0.10
TARGET1_PERCENT = 0.15
TARGET2_PERCENT = 0.25

POLL_SECONDS = 30


# ============================================================
# API
# ============================================================

def api_get(endpoint, params=None):

    url = BASE_URL + endpoint

    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=15
    )

    if response.status_code != 200:

        print(
            f"API ERROR {response.status_code}: "
            f"{response.text}"
        )

        return None

    return response.json()


# ============================================================
# OPTION CHAIN
# ============================================================

def get_option_chain(underlying_key):

    data = api_get(
        "/option/chain",
        {
            "instrument_key": underlying_key,
            "expiry_date": EXPIRY
        }
    )

    if not data:
        return None

    return data.get("data", [])


# ============================================================
# DETERMINE MARKET DIRECTION
# ============================================================

def determine_direction(option_chain):

    if not option_chain:
        return None

    total_call_oi = 0
    total_put_oi = 0

    spot = None

    for row in option_chain:

        spot = row.get("underlying_spot_price")

        call = row.get("call_options", {})
        put = row.get("put_options", {})

        call_md = call.get("market_data", {})
        put_md = put.get("market_data", {})

        total_call_oi += call_md.get("oi", 0) or 0
        total_put_oi += put_md.get("oi", 0) or 0

    if not spot:
        return None

    if total_put_oi > total_call_oi * 1.10:

        direction = "BULLISH"

    elif total_call_oi > total_put_oi * 1.10:

        direction = "BEARISH"

    else:

        direction = "NEUTRAL"

    return {
        "spot": spot,
        "call_oi": total_call_oi,
        "put_oi": total_put_oi,
        "direction": direction
    }


# ============================================================
# SELECT OPTION
# ============================================================

def select_option(option_chain, direction):

    if not option_chain:
        return None

    spot = option_chain[0].get(
        "underlying_spot_price"
    )

    if not spot:
        return None

    # Find ATM
    nearest = min(
        option_chain,
        key=lambda x: abs(
            x["strike_price"] - spot
        )
    )

    strike = nearest["strike_price"]

    if direction == "BULLISH":

        option_type = "CE"

        option = nearest.get("call_options")

    elif direction == "BEARISH":

        option_type = "PE"

        option = nearest.get("put_options")

    else:

        return None

    if not option:
        return None

    market_data = option.get(
        "market_data", {}
    )

    greeks = option.get(
        "option_greeks", {}
    )

    ltp = market_data.get("ltp", 0)

    if not ltp or ltp <= 0:
        return None

    return {

        "option_type": option_type,

        "strike": strike,

        "instrument_key":
            option.get("instrument_key"),

        "ltp": ltp,

        "bid": market_data.get("bid_price", 0),

        "ask": market_data.get("ask_price", 0),

        "volume": market_data.get("volume", 0),

        "oi": market_data.get("oi", 0),

        "delta": greeks.get("delta"),

        "iv": greeks.get("iv"),

        "spot": spot
    }


# ============================================================
# CONFIDENCE SCORE
# ============================================================

def calculate_confidence(
    direction_data,
    option
):

    score = 0

    direction = direction_data["direction"]

    call_oi = direction_data["call_oi"]
    put_oi = direction_data["put_oi"]

    # OI confirmation
    if direction == "BULLISH":

        if put_oi > call_oi:
            score += 25

    elif direction == "BEARISH":

        if call_oi > put_oi:
            score += 25

    # Liquidity
    if option["volume"] > 0:

        score += 20

    # Open interest
    if option["oi"] > 0:

        score += 20

    # Bid/ask spread
    bid = option["bid"]
    ask = option["ask"]

    if bid > 0 and ask > 0:

        spread = (ask - bid) / bid

        if spread < 0.02:

            score += 20

        elif spread < 0.05:

            score += 10

    return min(score, 100)


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_levels(entry):

    stop_loss = entry * (
        1 - STOP_LOSS_PERCENT
    )

    target1 = entry * (
        1 + TARGET1_PERCENT
    )

    target2 = entry * (
        1 + TARGET2_PERCENT
    )

    return {
        "entry": round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2)
    }


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(signal):

    filename = "trades.csv"

    file_exists = os.path.exists(filename)

    fields = [
        "timestamp",
        "underlying",
        "direction",
        "option_type",
        "strike",
        "instrument_key",
        "spot",
        "ltp",
        "entry",
        "stop_loss",
        "target1",
        "target2",
        "confidence"
    ]

    with open(
        filename,
        "a",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        if not file_exists:

            writer.writeheader()

        writer.writerow(signal)


# ============================================================
# SCAN ONE UNDERLYING
# ============================================================

def scan_underlying(
    name,
    instrument_key
):

    print("\n" + "=" * 70)

    print(
        f"Scanning {name}"
    )

    chain = get_option_chain(
        instrument_key
    )

    if not chain:

        print(
            "Option chain unavailable"
        )

        return

    direction_data = determine_direction(
        chain
    )

    if not direction_data:

        print(
            "Unable to determine direction"
        )

        return

    direction = direction_data[
        "direction"
    ]

    print(
        f"Spot       : "
        f"{direction_data['spot']}"
    )

    print(
        f"Call OI   : "
        f"{direction_data['call_oi']}"
    )

    print(
        f"Put OI    : "
        f"{direction_data['put_oi']}"
    )

    print(
        f"Direction : {direction}"
    )

    if direction == "NEUTRAL":

        print(
            "No trade — market neutral"
        )

        return

    option = select_option(
        chain,
        direction
    )

    if not option:

        print(
            "No suitable option found"
        )

        return

    confidence = calculate_confidence(
        direction_data,
        option
    )

    if confidence < MIN_CONFIDENCE:

        print(
            f"Confidence {confidence}% "
            f"< {MIN_CONFIDENCE}%"
        )

        return

    levels = calculate_levels(
        option["ltp"]
    )

    signal = {

        "timestamp":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "underlying": name,

        "direction": direction,

        "option_type":
            option["option_type"],

        "strike":
            option["strike"],

        "instrument_key":
            option["instrument_key"],

        "spot":
            option["spot"],

        "ltp":
            option["ltp"],

        "entry":
            levels["entry"],

        "stop_loss":
            levels["stop_loss"],

        "target1":
            levels["target1"],

        "target2":
            levels["target2"],

        "confidence":
            confidence
    }

    print("\n******** SIGNAL ********")

    print(
        f"Underlying : {name}"
    )

    print(
        f"Signal     : BUY "
        f"{option['option_type']}"
    )

    print(
        f"Strike     : {option['strike']}"
    )

    print(
        f"Spot       : {option['spot']}"
    )

    print(
        f"Option LTP : ₹{option['ltp']}"
    )

    print(
        f"Entry      : ₹{levels['entry']}"
    )

    print(
        f"SL         : ₹{levels['stop_loss']}"
    )

    print(
        f"Target 1   : ₹{levels['target1']}"
    )

    print(
        f"Target 2   : ₹{levels['target2']}"
    )

    print(
        f"Confidence : {confidence}%"
    )

    print(
        "Mode       : PAPER"
    )

    print("************************")

    save_signal(signal)


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "UPSTOX_ANALYTICS_TOKEN "
            "not found in .env"
        )

    print(
        "Upstox Options Scanner Started"
    )

    print(
        "Mode: PAPER TRADING"
    )

    print(
        "Automatic CE/PE detection: ON"
    )

    while True:

        try:

            for name, key in (
                UNDERLYINGS.items()
            ):

                scan_underlying(
                    name,
                    key
                )

            print(
                f"\nNext scan in "
                f"{POLL_SECONDS} seconds..."
            )

            time.sleep(
                POLL_SECONDS
            )

        except KeyboardInterrupt:

            print(
                "\nScanner stopped."
            )

            break

        except Exception as e:

            print(
                f"ERROR: {e}"
            )

            time.sleep(10)


if __name__ == "__main__":

    main()
