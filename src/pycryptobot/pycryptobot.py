import sys
import click
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import json
import logging
import math
import os
import random
import re
import sched

from pycryptobot.models.trading import TechnicalAnalysis
from pycryptobot.models.trading_account import TradingAccount
from pycryptobot.models.coinbase_pro import AuthAPI, PublicAPI
from pycryptobot.views.trading_graphs import TradingGraphs
from pycryptobot.util import truncate
from pycryptobot.util import get_comparison_string
from pycryptobot.click_ext import PCBClickGroup

# production: disable traceback
sys.tracebacklimit = 0


CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help", "--im-dumb"],
    "max_content_width": 200,
}


def exit_on_interrupt(signal, frame):
    click.echo(err=True)
    sys.exit(1)


signal.signal(signal.SIGINT, exit_on_interrupt)

# reduce informational logging
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


@click.group(
    help="Python Crypto Bot consuming Coinbase Pro API",
    cls=PCBClickGroup,
    context_settings=CONTEXT_SETTINGS,
)
@shared_options()
def cli(state):
    pass


@click.command()
def run():
    """Run the trading bot."""


# preload config from config.json if it exists
try:
    # open the config.json file
    with open("config.json") as config_file:
        # store the configuration in dictionary
        config = json.load(config_file)

        if "config" in config:
            if "cryptoMarket" and "fiatMarket" in config["config"]:
                crypto_market = config["config"]["cryptoMarket"]
                fiat_market = config["config"]["fiatMarket"]

            if "granularity" in config["config"]:
                if isinstance(config["config"]["granularity"], int):
                    if config["config"]["granularity"] in [
                        60,
                        300,
                        900,
                        3600,
                        21600,
                        86400,
                    ]:
                        granularity = config["config"]["granularity"]

            if "graphs" in config["config"]:
                if isinstance(config["config"]["graphs"], int):
                    if config["config"]["graphs"] in [0, 1]:
                        save_graphs = config["config"]["graphs"]

            if "live" in config["config"]:
                if isinstance(config["config"]["live"], int):
                    if config["config"]["live"] in [0, 1]:
                        is_live = config["config"]["live"]

            if "verbose" in config["config"]:
                if isinstance(config["config"]["verbose"], int):
                    if config["config"]["verbose"] in [0, 1]:
                        is_verbose = config["config"]["verbose"]

            if "sim" in config["config"]:
                if isinstance(config["config"]["sim"], str):
                    if config["config"]["sim"] in [
                        "slow",
                        "fast",
                        "slow-sample",
                        "fast-sample",
                    ]:
                        is_live = 0
                        is_sim = 1
                        sim_speed = config["config"]["sim"]

            if "sellupperpcnt" in config["config"]:
                if isinstance(config["config"]["sellupperpcnt"], int):
                    if (
                        config["config"]["sellupperpcnt"] > 0
                        and config["config"]["sellupperpcnt"] <= 100
                    ):
                        sell_upper_pcnt = int(config["config"]["sellupperpcnt"])

            if "selllowerpcnt" in config["config"]:
                if isinstance(config["config"]["selllowerpcnt"], int):
                    if (
                        config["config"]["selllowerpcnt"] >= -100
                        and config["config"]["selllowerpcnt"] < 0
                    ):
                        sell_lower_pcnt = int(config["config"]["selllowerpcnt"])

except IOError:
    click.echo("warning: 'config.json' not found.")

if args.market != None:
    # market set via --market argument

    # validates the market is syntactically correct
    p = re.compile(r"^[A-Z]{3,4}\-[A-Z]{3,4}$")
    if not p.match(args.market):
        raise TypeError("Coinbase Pro market required.")

    crypto_market, fiat_market = args.market.split("-", 2)

# validation of crypto market inputs
if crypto_market not in ["BCH", "BTC", "ETH", "LTC", "XLM"]:
    raise Exception("Invalid crypto market: BCH, BTC, ETH, LTC, ETH, XLM")

# validation of fiat market inputs
if fiat_market not in ["EUR", "GBP", "USD"]:
    raise Exception("Invalid FIAT market: EUR, GBP, USD")

# reconstruct the market based on the crypto and fiat inputs
market = crypto_market + "-" + fiat_market

if args.granularity != None:
    # granularity set via --granularity argument

    # validates granularity is an integer
    if not isinstance(args.granularity, int):
        raise TypeError("Granularity integer required.")

    # validates the granularity is supported by Coinbase Pro
    if not args.granularity in [60, 300, 900, 3600, 21600, 86400]:
        raise TypeError("Granularity options: 60, 300, 900, 3600, 21600, 86400.")

    granularity = args.granularity

if args.graphs != None:
    # graphs status set via --graphs argument

    if args.graphs == 1:
        save_graphs = 1
    else:
        save_graphs = 0

if args.live != None:
    # live status set via --live argument

    if args.live == 1:
        is_live = 1
    else:
        is_live = 0

if args.verbose != None:
    # verbose status set via --verbose argument

    if args.verbose == 1:
        is_verbose = 1
    else:
        is_verbose = 0

if args.sim != None:
    # sim status set via --sim argument

    if args.sim == "slow":
        is_sim = 1
        sim_speed = "slow"
        is_live = 0
    elif args.sim == "slow-sample":
        is_sim = 1
        sim_speed = "slow-sample"
        is_live = 0
    elif args.sim == "fast":
        is_sim = 1
        sim_speed = "fast"
        is_live = 0
    elif args.sim == "fast-sample":
        is_sim = 1
        sim_speed = "fast-sample"
        is_live = 0

    else:
        is_sim = 0
        sim_speed = ""

if args.sellupperpcnt != None:
    # sell upper percent --sellupperlimit pcnt

    if isinstance(args.sellupperpcnt, int):
        if args.sellupperpcnt > 0 and args.sellupperpcnt <= 100:
            sell_upper_pcnt = int(args.sellupperpcnt)

if args.selllowerpcnt != None:
    # sell lower percent --selllowerlimit pcnt

    if isinstance(args.selllowerpcnt, int):
        if args.selllowerpcnt >= -100 and args.selllowerpcnt < 0:
            sell_lower_pcnt = int(args.selllowerpcnt)

# initial state is to wait
action = "WAIT"
last_action = ""
last_buy = 0
last_df_index = ""
buy_state = ""
iterations = 0
x_since_buy = 0
x_since_sell = 0
buy_count = 0
sell_count = 0
buy_sum = 0
sell_sum = 0
failsafe = False

config = {}
account = None
# if live trading is enabled
if is_live == 1:
    # open the config.json file
    with open("config.json") as config_file:
        # store the configuration in dictionary
        config = json.load(config_file)
    # connect your Coinbase Pro live account
    account = TradingAccount(config)

    # if the bot is restarted between a buy and sell it will sell first
    if market.startswith("BTC-") and account.getBalance(crypto_market) > 0.001:
        last_action = "BUY"
    elif market.startswith("BCH-") and account.getBalance(crypto_market) > 0.01:
        last_action = "BUY"
    elif market.startswith("ETH-") and account.getBalance(crypto_market) > 0.01:
        last_action = "BUY"
    elif market.startswith("LTC-") and account.getBalance(crypto_market) > 0.1:
        last_action = "BUY"
    elif market.startswith("XLM-") and account.getBalance(crypto_market) > 35:
        last_action = "BUY"
    elif account.getBalance(fiat_market) > 30:
        last_action = "SELL"

    authAPI = AuthAPI(
        config["api_key"], config["api_secret"], config["api_pass"], config["api_url"]
    )
    orders = authAPI.getOrders(market, "", "done")
    if len(orders) > 0:
        df = orders[-1:]
        price = df[df.action == "buy"]["price"]
        if len(price) > 0:
            last_buy = float(truncate(price, 2))


def executeJob(sc, market, granularity, tradingData=pd.DataFrame()):
    """Trading bot job which runs at a scheduled interval"""
    global action, buy_count, buy_sum, failsafe, iterations, last_action, last_buy, last_df_index, sell_count, sell_sum, buy_state, x_since_buy, x_since_sell

    # increment iterations
    iterations = iterations + 1

    # coinbase pro public api
    api = PublicAPI()

    if is_sim == 0:
        # retrieve the market data
        tradingData = api.getHistoricalData(market, granularity)

    df = pd.DataFrame()
    if len(tradingData) != 300:
        # data frame should have 300 rows, if not retry
        click.echo("error: data frame length is < 300 (" + str(len(tradingData)) + ")")
        logging.error(
            "error: data frame length is < 300 (" + str(len(tradingData)) + ")"
        )
        s.enter(1, 1, executeJob, (sc, market, granularity))
    else:
        # analyse the market data
        tradingDataCopy = tradingData.copy()
        technicalAnalysis = TechnicalAnalysis(tradingDataCopy)
        technicalAnalysis.addAll()
        df = technicalAnalysis.getDataFrame()

    if is_sim == 1:
        # with a simulation df_last will iterate through data
        df_last = df.iloc[iterations - 1 : iterations]
    else:
        # df_last contains the most recent entry
        df_last = df.tail(1)

    current_df_index = str(df_last.index.format()[0])

    if is_sim == 0:
        price = api.getTicker(market)
        if price < df_last["low"].values[0] or price == 0:
            price = float(df_last["close"].values[0])
    else:
        price = float(df_last["close"].values[0])

    ema12gtema26 = bool(df_last["ema12gtema26"].values[0])
    ema12gtema26co = bool(df_last["ema12gtema26co"].values[0])
    goldencross = bool(df_last["goldencross"].values[0])
    macdgtsignal = bool(df_last["macdgtsignal"].values[0])
    macdgtsignalco = bool(df_last["macdgtsignalco"].values[0])
    ema12ltema26 = bool(df_last["ema12ltema26"].values[0])
    ema12ltema26co = bool(df_last["ema12ltema26co"].values[0])
    macdltsignal = bool(df_last["macdltsignal"].values[0])
    macdltsignalco = bool(df_last["macdltsignalco"].values[0])
    obv = float(df_last["obv"].values[0])
    obv_pc = float(df_last["obv_pc"].values[0])

    # candlestick detection
    hammer = bool(df_last["hammer"].values[0])
    inverted_hammer = bool(df_last["inverted_hammer"].values[0])
    hanging_man = bool(df_last["hanging_man"].values[0])
    shooting_star = bool(df_last["shooting_star"].values[0])
    three_white_soldiers = bool(df_last["three_white_soldiers"].values[0])
    three_black_crows = bool(df_last["three_black_crows"].values[0])
    morning_star = bool(df_last["morning_star"].values[0])
    evening_star = bool(df_last["evening_star"].values[0])
    three_line_strike = bool(df_last["three_line_strike"].values[0])
    abandoned_baby = bool(df_last["abandoned_baby"].values[0])
    morning_doji_star = bool(df_last["morning_doji_star"].values[0])
    evening_doji_star = bool(df_last["evening_doji_star"].values[0])
    two_black_gapping = bool(df_last["two_black_gapping"].values[0])

    # criteria for a buy signal
    if (
        (ema12gtema26co == True and macdgtsignal == True and obv_pc > 1)
        or (ema12gtema26 and macdgtsignal and obv_pc > 1 and 0 < x_since_buy <= 2)
    ) and last_action != "BUY":
        action = "BUY"
    # criteria for a sell signal
    elif (
        (ema12ltema26co and macdltsignal)
        or ema12ltema26
        and macdltsignal
        and 0 < x_since_sell <= 2
    ) and last_action not in ["", "SELL"]:
        action = "SELL"
        failsafe = False
    # anything other than a buy or sell, just wait
    else:
        action = "WAIT"

    if last_buy > 0 and last_action == "BUY":
        change_pcnt = ((price / last_buy) - 1) * 100

        # loss failsafe sell at sell_lower_pcnt
        if change_pcnt < sell_lower_pcnt:
            failsafe = True
            action = "SELL"
            last_action = "BUY"
            log_text = "! Loss Failsafe Triggered (< " + str(sell_lower_pcnt) + "%)"
            click.echo(log_text, "\n")
            logging.warning(log_text)

        # profit bank at sell_upper_pcnt
        if change_pcnt > sell_upper_pcnt:
            failsafe = True
            action = "SELL"
            last_action = "BUY"
            log_text = "! Profit Bank Triggered (> " + str(sell_upper_pcnt) + "%)"
            click.echo(log_text, "\n")
            logging.warning(log_text)

    golden_death_text = " (BULL)" if goldencross else " (BEAR)"

    # polling is every 5 minutes (even for hourly intervals), but only process once per interval
    if last_df_index != current_df_index:
        precision = 2
        if crypto_market == "XLM":
            precision = 4

        price_text = "Close: " + str(truncate(price, precision))
        ema_text = get_comparison_string(
            df_last["ema12"].values[0],
            df_last["ema26"].values[0],
            "EMA12/26",
            precision,
        )
        macd_text = get_comparison_string(
            df_last["macd"].values[0], df_last["signal"].values[0], "MACD", precision
        )
        obv_text = get_comparison_string(df_last["obv_pc"].values[0], 0.1, "OBV %", precision)
        counter_text = (
            "[I:"
            + str(iterations)
            + ",B:"
            + str(x_since_buy)
            + ",S:"
            + str(x_since_sell)
            + "]"
        )

        if hammer:
            log_text = '* Candlestick Detected: Hammer ("Weak - Reversal - Bullish Signal - Up")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if shooting_star:
            log_text = '* Candlestick Detected: Shooting Star ("Weak - Reversal - Bearish Pattern - Down")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if hanging_man:
            log_text = '* Candlestick Detected: Hanging Man ("Weak - Continuation - Bearish Pattern - Down")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if inverted_hammer:
            log_text = '* Candlestick Detected: Inverted Hammer ("Weak - Continuation - Bullish Pattern - Up")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if three_white_soldiers:
            log_text = '*** Candlestick Detected: Three White Soldiers ("Strong - Reversal - Bullish Pattern - Up")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if three_black_crows:
            log_text = '* Candlestick Detected: Three Black Crows ("Strong - Reversal - Bearish Pattern - Down")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if morning_star:
            log_text = '*** Candlestick Detected: Morning Star ("Strong - Reversal - Bullish Pattern - Up")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if evening_star:
            log_text = '*** Candlestick Detected: Evening Star ("Strong - Reversal - Bearish Pattern - Down")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if three_line_strike:
            log_text = '** Candlestick Detected: Three Line Strike ("Reliable - Reversal - Bullish Pattern - Up")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if abandoned_baby:
            log_text = '** Candlestick Detected: Abandoned Baby ("Reliable - Reversal - Bullish Pattern - Up")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if morning_doji_star:
            log_text = '** Candlestick Detected: Morning Doji Star ("Reliable - Reversal - Bullish Pattern - Up")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if evening_doji_star:
            log_text = '** Candlestick Detected: Evening Doji Star ("Reliable - Reversal - Bearish Pattern - Down")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        if two_black_gapping:
            log_text = '*** Candlestick Detected: Two Black Gapping ("Reliable - Reversal - Bearish Pattern - Down")'
            click.echo(log_text, "\n")
            logging.debug(log_text)

        ema_co_prefix = ""
        ema_co_suffix = ""
        if ema12gtema26co:
            ema_co_prefix = "*^ "
            ema_co_suffix = " ^*"
        elif ema12ltema26co:
            ema_co_prefix = "*v "
            ema_co_suffix = " v*"
        elif ema12gtema26:
            ema_co_prefix = "^ "
            ema_co_suffix = " ^"
        elif ema12ltema26:
            ema_co_prefix = "v "
            ema_co_suffix = " v"

        macd_co_prefix = ""
        macd_co_suffix = ""
        if macdgtsignalco:
            macd_co_prefix = "*^ "
            macd_co_suffix = " ^*"
        elif macdltsignalco:
            macd_co_prefix = "*v "
            macd_co_suffix = " v*"
        elif macdgtsignal:
            macd_co_prefix = "^ "
            macd_co_suffix = " ^"
        elif macdltsignal:
            macd_co_prefix = "v "
            macd_co_suffix = " v"

        if obv_pc > 0.1:
            obv_prefix = "^ "
            obv_suffix = " ^"
        else:
            obv_prefix = "v "
            obv_suffix = " v"

        if is_verbose == 0:
            output_text = (
                f"f{current_df_index} | "
                f"{market}{golden_death_text} | "
                f"{str(granularity)} | "
                f"{price_text} | "
                f"{ema_co_prefix}{ema_text}{ema_co_suffix} | "
                f"{macd_co_prefix}{macd_text}{macd_co_suffix} | "
                f"{obv_prefix}{obv_text}{obv_suffix} | {action} {counter_text}"
            )
            if last_action != "":
                output_text = f"{output_text} | Last Action: {last_action}"

            if last_action == "BUY":
                # calculate last buy minus fees
                fee = last_buy * 0.005
                last_buy_minus_fees = last_buy + fee

                margin = (
                    str(truncate((((price - last_buy_minus_fees) / price) * 100), 2))
                    + "%"
                )
                output_text += " | " + margin

            logging.debug(output_text)
            click.echo(output_text)
        else:
            logging.debug(
                "-- Iteration: " + str(iterations) + " --" + golden_death_text
            )
            logging.debug("-- Since Last Buy: " + str(x_since_buy) + " --")
            logging.debug("-- Since Last Sell: " + str(x_since_sell) + " --")

            if last_action == "BUY":
                margin = str(truncate((((price - last_buy) / price) * 100), 2)) + "%"
                logging.debug("-- Margin: " + margin + "% --")

            logging.debug("price: " + str(truncate(price, 2)))
            logging.debug(
                "ema12: " + str(truncate(float(df_last["ema12"].values[0]), 2))
            )
            logging.debug(
                "ema26: " + str(truncate(float(df_last["ema26"].values[0]), 2))
            )
            logging.debug("ema12gtema26co: " + str(ema12gtema26co))
            logging.debug("ema12gtema26: " + str(ema12gtema26))
            logging.debug("ema12ltema26co: " + str(ema12ltema26co))
            logging.debug("ema12ltema26: " + str(ema12ltema26))
            logging.debug("macd: " + str(truncate(float(df_last["macd"].values[0]), 2)))
            logging.debug(
                "signal: " + str(truncate(float(df_last["signal"].values[0]), 2))
            )
            logging.debug("macdgtsignal: " + str(macdgtsignal))
            logging.debug("macdltsignal: " + str(macdltsignal))
            logging.debug("obv: " + str(obv))
            logging.debug("obv_pc: " + str(obv_pc) + "%")
            logging.debug("action: " + action)

            # informational output on the most recent entry
            click.echo("")
            click.echo(
                "================================================================================"
            )
            txt = "        Iteration : " + str(iterations) + golden_death_text
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "   Since Last Buy : " + str(x_since_buy)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "  Since Last Sell : " + str(x_since_sell)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "        Timestamp : " + str(df_last.index.format()[0])
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            click.echo(
                "--------------------------------------------------------------------------------"
            )
            txt = "            Close : " + str(truncate(price, 2))
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "            EMA12 : " + str(
                truncate(float(df_last["ema12"].values[0]), 2)
            )
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "            EMA26 : " + str(
                truncate(float(df_last["ema26"].values[0]), 2)
            )
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "   Crossing Above : " + str(ema12gtema26co)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "  Currently Above : " + str(ema12gtema26)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "   Crossing Below : " + str(ema12ltema26co)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "  Currently Below : " + str(ema12ltema26)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")

            if ema12gtema26 == True and ema12gtema26co == True:
                txt = "        Condition : EMA12 is currently crossing above EMA26"
            elif ema12gtema26 == True and ema12gtema26co == False:
                txt = "        Condition : EMA12 is currently above EMA26 and has crossed over"
            elif ema12ltema26 == True and ema12ltema26co == True:
                txt = "        Condition : EMA12 is currently crossing below EMA26"
            elif ema12ltema26 == True and ema12ltema26co == False:
                txt = "        Condition : EMA12 is currently below EMA26 and has crossed over"
            else:
                txt = "        Condition : -"
            click.echo("|", txt, (" " * (75 - len(txt))), "|")

            click.echo(
                "--------------------------------------------------------------------------------"
            )
            txt = "             MACD : " + str(
                truncate(float(df_last["macd"].values[0]), 2)
            )
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "           Signal : " + str(
                truncate(float(df_last["signal"].values[0]), 2)
            )
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "  Currently Above : " + str(macdgtsignal)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "  Currently Below : " + str(macdltsignal)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")

            if macdgtsignal == True and macdgtsignalco == True:
                txt = "        Condition : MACD is currently crossing above Signal"
            elif macdgtsignal == True and macdgtsignalco == False:
                txt = "        Condition : MACD is currently above Signal and has crossed over"
            elif macdltsignal == True and macdltsignalco == True:
                txt = "        Condition : MACD is currently crossing below Signal"
            elif macdltsignal == True and macdltsignalco == False:
                txt = "        Condition : MACD is currently below Signal and has crossed over"
            else:
                txt = "        Condition : -"
            click.echo("|", txt, (" " * (75 - len(txt))), "|")

            click.echo(
                "--------------------------------------------------------------------------------"
            )
            txt = "              OBV : " + str(truncate(obv, 4))
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "       OBV Change : " + str(obv_pc) + "%"
            click.echo("|", txt, (" " * (75 - len(txt))), "|")

            if obv_pc >= 2:
                txt = "        Condition : Large positive volume changes"
            elif obv_pc < 2 and obv_pc >= 0:
                txt = "        Condition : Positive volume changes"
            else:
                txt = "        Condition : Negative volume changes"
            click.echo("|", txt, (" " * (75 - len(txt))), "|")

            click.echo(
                "--------------------------------------------------------------------------------"
            )
            txt = "           Action : " + action
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            click.echo(
                "================================================================================"
            )
            if last_action == "BUY":
                txt = "           Margin : " + margin + "%"
                click.echo("|", txt, (" " * (75 - len(txt))), "|")
                click.echo(
                    "================================================================================"
                )

        # increment x since buy
        if ema12gtema26 == True and failsafe == False:
            if buy_state == "":
                buy_state = "NO_BUY"

            if buy_state != "NO_BUY" or buy_state == "NORMAL":
                x_since_buy = x_since_buy + 1

        # increment x since sell
        elif ema12ltema26:
            x_since_sell = x_since_sell + 1
            buy_state = "NORMAL"
            failsafe = False

        # if a buy signal
        if action == "BUY":
            buy_count = buy_count + 1

            # reset x since sell
            x_since_sell = 0

            last_buy = price

            # if live
            if is_live == 1:
                if is_verbose == 0:
                    logging.info(
                        current_df_index
                        + " | "
                        + market
                        + " "
                        + str(granularity)
                        + " | "
                        + price_text
                        + " | BUY"
                    )
                    click.echo(
                        "\n",
                        current_df_index,
                        "|",
                        market,
                        granularity,
                        "|",
                        price_text,
                        "| BUY",
                        "\n",
                    )
                else:
                    click.echo(
                        "--------------------------------------------------------------------------------"
                    )
                    click.echo(
                        "|                      *** Executing LIVE Buy Order ***                        |"
                    )
                    click.echo(
                        "--------------------------------------------------------------------------------"
                    )
                # connect to coinbase pro api (authenticated)
                model = AuthAPI(
                    config["api_key"],
                    config["api_secret"],
                    config["api_pass"],
                    config["api_url"],
                )
                # execute a live market buy
                resp = model.marketBuy(market, float(account.getBalance(fiat_market)))
                logging.info(resp)
                # logging.info('attempt to buy ' + resp['specified_funds'] + ' (' + resp['funds'] + ' after fees) of ' + resp['product_id'])
            # if not live
            else:
                if is_verbose == 0:
                    logging.info(
                        current_df_index
                        + " | "
                        + market
                        + " "
                        + str(granularity)
                        + " | "
                        + price_text
                        + " | BUY"
                    )
                    click.echo(
                        "\n",
                        current_df_index,
                        "|",
                        market,
                        granularity,
                        "|",
                        price_text,
                        "| BUY",
                    )
                    click.echo(
                        " Fibonacci Retracement Levels:",
                        str(
                            technicalAnalysis.getFibonacciRetracementLevels(
                                float(price)
                            )
                        ),
                        "\n",
                    )
                else:
                    click.echo(
                        "--------------------------------------------------------------------------------"
                    )
                    click.echo(
                        "|                      *** Executing TEST Buy Order ***                        |"
                    )
                    click.echo(
                        "--------------------------------------------------------------------------------"
                    )
                # click.echo(df_last[['close','ema12','ema26','ema12gtema26','ema12gtema26co','macd','signal','macdgtsignal','obv','obv_pc']])

            if save_graphs == 1:
                tradinggraphs = TradingGraphs(technicalAnalysis)
                ts = datetime.now().timestamp()
                filename = "BTC-GBP_3600_buy_" + str(ts) + ".png"
                tradinggraphs.renderEMAandMACD(24, "graphs/" + filename, True)

        # if a sell signal
        elif action == "SELL":
            sell_count = sell_count + 1

            # reset x since buy
            x_since_buy = 0

            # if live
            if is_live == 1:
                if is_verbose == 0:
                    logging.info(
                        current_df_index
                        + " | "
                        + market
                        + " "
                        + str(granularity)
                        + " | "
                        + price_text
                        + " | SELL"
                    )
                    click.echo(
                        "\n",
                        current_df_index,
                        "|",
                        market,
                        granularity,
                        "|",
                        price_text,
                        "| SELL",
                    )
                    click.echo(
                        " Fibonacci Retracement Levels:",
                        str(
                            technicalAnalysis.getFibonacciRetracementLevels(
                                float(price)
                            )
                        ),
                        "\n",
                    )
                else:
                    click.echo(
                        "--------------------------------------------------------------------------------"
                    )
                    click.echo(
                        "|                      *** Executing LIVE Sell Order ***                        |"
                    )
                    click.echo(
                        "--------------------------------------------------------------------------------"
                    )
                # connect to Coinbase Pro API live
                model = AuthAPI(
                    config["api_key"],
                    config["api_secret"],
                    config["api_pass"],
                    config["api_url"],
                )
                # execute a live market sell
                resp = model.marketSell(market, float(account.getBalance(crypto_market)))
                logging.info(resp)
                # logging.info('attempt to sell ' + resp['size'] + ' of ' + resp['product_id'])
            # if not live
            else:
                if is_verbose == 1:
                    click.echo(
                        "--------------------------------------------------------------------------------"
                    )
                    click.echo(
                        "|                      *** Executing TEST Sell Order ***                        |"
                    )
                    click.echo(
                        "--------------------------------------------------------------------------------"
                    )

                sell_price = float(str(truncate(price, precision)))
                last_buy_price = float(str(truncate(float(last_buy), precision)))
                buy_sell_diff = round(
                    np.subtract(sell_price, last_buy_price), precision
                )
                buy_sell_margin_no_fees = (
                    str(
                        truncate(
                            (((sell_price - last_buy_price) / sell_price) * 100), 2
                        )
                    )
                    + "%"
                )

                # calculate last buy minus fees
                buy_fee = last_buy_price * 0.005
                last_buy_price_minus_fees = last_buy_price + buy_fee

                buy_sell_margin_fees = (
                    str(
                        truncate(
                            (
                                ((sell_price - last_buy_price_minus_fees) / sell_price)
                                * 100
                            ),
                            2,
                        )
                    )
                    + "%"
                )

                logging.info(
                    current_df_index
                    + " | "
                    + market
                    + " "
                    + str(granularity)
                    + " | SELL | "
                    + str(sell_price)
                    + " | BUY | "
                    + str(last_buy_price)
                    + " | DIFF | "
                    + str(buy_sell_diff)
                    + " | MARGIN NO FEES | "
                    + str(buy_sell_margin_no_fees)
                    + " | MARGIN FEES | "
                    + str(buy_sell_margin_fees)
                )
                click.echo(
                    "\n",
                    current_df_index,
                    "|",
                    market,
                    granularity,
                    "| SELL |",
                    str(sell_price),
                    "| BUY |",
                    str(last_buy_price),
                    "| DIFF |",
                    str(buy_sell_diff),
                    "| MARGIN NO FEES |",
                    str(buy_sell_margin_no_fees),
                    "| MARGIN FEES |",
                    str(buy_sell_margin_fees),
                    "\n",
                )

                buy_sum = buy_sum + last_buy_price_minus_fees
                sell_sum = sell_sum + sell_price

            # click.echo(df_last[['close','ema12','ema26','ema12ltema26','ema12ltema26co','macd','signal','macdltsignal','obv','obv_pc']])

            if save_graphs == 1:
                tradinggraphs = TradingGraphs(technicalAnalysis)
                ts = datetime.now().timestamp()
                filename = "BTC-GBP_3600_buy_" + str(ts) + ".png"
                tradinggraphs.renderEMAandMACD(24, "graphs/" + filename, True)

        # last significant action
        if action in ["BUY", "SELL"]:
            last_action = action

        last_df_index = str(df_last.index.format()[0])

        if iterations == 300:
            click.echo("\nSimulation Summary\n")

            if buy_count > sell_count:
                # calculate last buy minus fees
                fee = last_buy * 0.005
                last_buy_minus_fees = last_buy + fee

                buy_sum = buy_sum + (
                    float(truncate(price, precision)) - last_buy_minus_fees
                )

            click.echo("   Buy Count :", buy_count)
            click.echo("  Sell Count :", sell_count, "\n")

            margin_decimal = (sell_sum - buy_sum) / sell_sum if sell_sum else 0
            click.echo(
                "      Margin :", str(truncate((margin_decimal * 100), 2)) + "%", "\n"
            )
    else:
        now = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
        click.echo(
            now,
            "|",
            market + golden_death_text,
            "|",
            str(granularity),
            "| Current Price:",
            price,
        )

        # decrement ignored iteration
        iterations = iterations - 1

    # if live
    if is_live == 1:
        # update order tracker csv
        account.saveTrackerCSV()

    if is_sim == 1:
        if iterations < 300:
            if sim_speed in ["fast", "fast-sample"]:
                # fast processing
                executeJob(sc, market, granularity, tradingData)
            else:
                # slow processing
                s.enter(1, 1, executeJob, (sc, market, granularity, tradingData))

    else:
        # poll every 5 minute
        s.enter(300, 1, executeJob, (sc, market, granularity))


try:
    logging.basicConfig(
        filename="../../pycryptobot.log",
        format="%(asctime)s - %(levelname)s: %(message)s",
        datefmt="%m/%d/%Y %I:%M:%S %p",
        filemode="a",
        level=logging.DEBUG,
    )

    click.echo(
        "--------------------------------------------------------------------------------"
    )
    click.echo(
        "|                Python Crypto Bot using the Coinbase Pro API                  |"
    )
    click.echo(
        "--------------------------------------------------------------------------------"
    )

    if is_verbose == 1:
        txt = "           Market : " + market
        click.echo("|", txt, (" " * (75 - len(txt))), "|")
        txt = "      Granularity : " + str(granularity) + " seconds"
        click.echo("|", txt, (" " * (75 - len(txt))), "|")
        click.echo(
            "--------------------------------------------------------------------------------"
        )

    if is_live == 1:
        txt = "         Bot Mode : LIVE - live trades using your funds!"
    else:
        txt = "         Bot Mode : TEST - test trades using dummy funds :)"

    click.echo("|", txt, (" " * (75 - len(txt))), "|")

    txt = "      Bot Started : " + str(datetime.now())
    click.echo("|", txt, (" " * (75 - len(txt))), "|")
    click.echo(
        "================================================================================"
    )
    if sell_upper_pcnt != 101:
        txt = "       Sell Upper : " + str(sell_upper_pcnt) + "%"
        click.echo("|", txt, (" " * (75 - len(txt))), "|")

    if sell_lower_pcnt != -101:
        txt = "       Sell Lower : " + str(sell_lower_pcnt) + "%"
        click.echo("|", txt, (" " * (75 - len(txt))), "|")

    if sell_upper_pcnt != 101 or sell_lower_pcnt != 101:
        click.echo(
            "================================================================================"
        )

    # if live
    if is_live == 1:
        # if live, ensure sufficient funds to place next buy order
        if (last_action == "" or last_action == "SELL") and account.getBalance(
            fiat_market
        ) == 0:
            raise Exception(
                "Insufficient " + fiat_market + " funds to place next buy order!"
            )
        # if live, ensure sufficient crypto to place next sell order
        elif last_action == "BUY" and account.getBalance(crypto_market) == 0:
            raise Exception(
                "Insufficient " + crypto_market + " funds to place next sell order!"
            )

    s = sched.scheduler(time.time, time.sleep)
    # run the first job immediately after starting
    if is_sim == 1:
        api = PublicAPI()

        if sim_speed in ["fast-sample", "slow-sample"]:
            tradingData = pd.DataFrame()

            attempts = 0
            while len(tradingData) != 300 and attempts < 10:
                endDate = datetime.now() - timedelta(
                    hours=random.randint(0, 8760 * 3)
                )  # 3 years in hours
                startDate = endDate - timedelta(hours=300)
                tradingData = api.getHistoricalData(
                    market, granularity, startDate.isoformat(), endDate.isoformat()
                )
                attempts += 1

            if len(tradingData) != 300:
                raise Exception(
                    "Unable to retrieve 300 random sets of data between "
                    + str(startDate)
                    + " and "
                    + str(endDate)
                    + " in "
                    + str(attempts)
                    + " attempts."
                )

            startDate = str(startDate.isoformat())
            endDate = str(endDate.isoformat())
            txt = "   Sampling start : " + str(startDate)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            txt = "     Sampling end : " + str(endDate)
            click.echo("|", txt, (" " * (75 - len(txt))), "|")
            click.echo(
                "================================================================================"
            )
        else:
            tradingData = api.getHistoricalData(market, granularity)

        executeJob(s, market, granularity, tradingData)
    else:
        executeJob(s, market, granularity)

    s.run()

# catches a keyboard break of app, exits gracefully
except KeyboardInterrupt:
    click.echo(datetime.now(), "closed")
    try:
        sys.exit(0)
    except SystemExit:
        os._exit(0)
