#!/usr/bin/env python3

import calendar
import csv
import time
import datetime
import io
import os
import subprocess
import sys
import re
import argparse

from decimal import Decimal


parser = argparse.ArgumentParser(
    description='Export hledger data into OpenMetrics format')
parser.add_argument('--file', default=None, required=False)
arguments = parser.parse_args()


def hledger_command(args):
    """Run a hledger command, throw an error if it fails, and return the
    stdout.
    """

    real_args = ["hledger"]
    if arguments.file:
        real_args.extend(['--file', arguments.file])
    real_args.extend(args)

    proc = subprocess.run(real_args, check=True, capture_output=True)
    return proc.stdout.decode("utf-8")


def parse_date(date):
    if len(date.split("-")) == 2:
        return datetime.datetime.strptime(date, "%Y-%m")
    else:
        return datetime.datetime.strptime(date, "%Y-%m-%d")


def pivot(samples_by_timestamp):
    """Turn `timestamp => key => value` to `key => [timestamp, value]`"""

    pivoted = {}
    for timestamp, kvs in samples_by_timestamp.items():
        for k, v in kvs.items():
            samples = pivoted.get(k, [])
            samples.append([timestamp, v])
            pivoted[k] = samples
    return pivoted


def metric_hledger_fx_rate(current_fx_rates, timestamps, target_currencies=["SEK", "USD", "EUR", "RUB"]):
    """
    `hledger_fx_rate{currency="xxx", target_currency="xxx"}`

    - Every target currency has an exchange rate of 1 with itself.

    Exchange rates are projected forwards if there are credits /
    debits in a gap.
    """

    def key(currency, target_currency): return (
        ("currency", currency),
        ("target_currency", target_currency),
    )

    all_timestamps = {timestamp: True for timestamp in timestamps}

    price_entry = re.compile(r'P'
                             r' (?P<date>\d\d\d\d-\d\d-\d\d)'
                             r' (?P<from_currency>([\(\)\-\w\d]+)|("[\(\)\-\w\d ]+"))'
                             r' (?P<exchange_rate>(\d+,)?\d+(.\d+)?)'
                             r' (?P<to_currency>([\(\)\-\w\d]+)|("[\(\)\-\w\d ]+"))')

    # given_rates_by_timestamp :: timestamp => currency => target_currency => exchange_rate
    given_rates_by_timestamp = {}
    for price in current_fx_rates:
        m = price_entry.match(price)
        date = m.group('date')
        from_currency = m.group('from_currency')
        exchange_rate = m.group('exchange_rate')
        to_currency = m.group('to_currency')

        timestamp = parse_date(date)
        all_timestamps[timestamp] = True
        from_currency = from_currency.replace('"', '')
        to_currency = to_currency.replace('"', '')
        exchange_rate = Decimal(exchange_rate.replace(',', ''))

        new_rates = given_rates_by_timestamp.get(timestamp, {})
        from_rates = new_rates.get(from_currency, {})
        from_rates[to_currency] = exchange_rate
        new_rates[from_currency] = from_rates
        given_rates_by_timestamp[timestamp] = new_rates

    # fx_rates_by_timestamp :: timestamp => key => exchange_rate
    fx_rates_by_timestamp = {}
    latest_rates = {}
    for timestamp in sorted(all_timestamps.keys()):
        timestamp_rates = given_rates_by_timestamp.get(timestamp, {})
        latest_rates = {
            **latest_rates,
            **timestamp_rates,
        }

        def get_fx_rate(currency, target_currency):
            if currency == target_currency:
                return 1
            elif currency in latest_rates and target_currency in latest_rates[currency]:
                return latest_rates[currency][target_currency]
            elif target_currency in latest_rates and currency in latest_rates[target_currency]:
                return 1/latest_rates[target_currency][currency]
            else:
                for intermediate in latest_rates[currency]:
                    intermediate_fx = get_fx_rate(
                        intermediate, target_currency)
                    if intermediate_fx is not None:
                        return latest_rates[currency][intermediate] * intermediate_fx
                return None

        fx_rates = {}
        for target_currency in target_currencies:
            for currency in target_currencies:
                fx = get_fx_rate(currency, target_currency)
                if fx is None:
                    raise Exception(
                        f'{timestamp}: can not convert {currency} to {target_currency}')
                fx_rates[key(currency, target_currency)] = fx

            for currency in latest_rates.keys():
                fx = get_fx_rate(currency, target_currency)
                if fx is None:
                    raise Exception(
                        f'{timestamp}: can not convert {currency} to {target_currency}')
                fx_rates[key(currency, target_currency)] = fx

        fx_rates_by_timestamp[timestamp] = fx_rates

    return pivot(fx_rates_by_timestamp)


def parse_balance(raw):
    if raw == '0':
        return 0, None
    elif len(raw) == 0:
        return None, None

    balance_re = re.compile(
        r'(?P<amount>-?(\d+,)?\d+(.\d+)?)'
        r' (?P<currency>([\(\)\-\w\d]+)|("[\(\)\-\w\d ]+"))'
    )

    m = balance_re.match(raw)
    if not m:
        raise Exception(f'unexpexted balance: "{raw}"')
    return Decimal(m["amount"].replace(",", "")), m["currency"].replace('"', '')


def parse_balances(raw):
    return list(map(parse_balance, raw.split(', ')))


def metric_hledger_balance(daily_balances):
    """`hledger_transactions{account="xxx", currency="xxx"}`
    """

    def key(account, currency): return (
        ("account", account), ("currency", currency))

    # deltas_by_timestamp :: timestamp => key => delta
    deltas_by_timestamp = {}
    for daily_balance in daily_balances:
        account = daily_balance["account"]

        if account == "total":
            continue

        del daily_balance["account"]
        for date, balance in daily_balance.items():
            if balance == '0':
                continue

            amount, currency = parse_balance(balance)
            timestamp = parse_date(date)
            deltas = deltas_by_timestamp.get(timestamp, {})
            deltas[key(account, currency)] = amount
            deltas_by_timestamp[timestamp] = deltas

    return pivot(deltas_by_timestamp)


def metric_hledger_transactions(daily_transactions):
    """`hledger_transactions{account="xxx", currency="xxx"}`
    """

    def key(account, currency): return (
        ("account", account), ("currency", currency))

    # deltas_by_timestamp :: timestamp => key => delta
    deltas_by_timestamp = {}
    for daily_transaction in daily_transactions:
        account = daily_transaction["account"]
        if account == "total":
            continue

        del daily_transaction["account"]
        for date, balance in daily_transaction.items():
            if balance == '0':
                continue

            timestamp = parse_date(date)
            amount, currency = parse_balance(balance)

            deltas = deltas_by_timestamp.get(timestamp, {})
            deltas[key(account, currency)] = amount
            deltas_by_timestamp[timestamp] = deltas

    return pivot(deltas_by_timestamp)


def metric_hledger_budget(monthly_budgets):
    def key(account, currency): return (
        ("account", account), ("currency", currency))

    def even_items(row):
        filtered = []
        for i, budget in enumerate(row):
            if i % 2 == 0:
                filtered.append(budget)
        return filtered

    def odd_items(row):
        filtered = []
        for i, budget in enumerate(row):
            if i % 2 != 0:
                filtered.append(budget)
        return filtered

    timestamps = list(
        map(parse_date, even_items(monthly_budgets[0][1:])))

    budgets_by_timestamp = {}
    for budget in monthly_budgets[1:-1]:
        account = budget[0]
        by_timestamp = list(map(parse_balances, odd_items(budget[1:])))
        for i, timestamp in enumerate(timestamps):
            for balance, currency in by_timestamp[i]:
                if currency is None:
                    continue

                new_budgets = budgets_by_timestamp.get(timestamp, {})
                new_budgets[key(account, currency)] = balance
                budgets_by_timestamp[timestamp] = new_budgets

    return pivot(budgets_by_timestamp)


daily_balances = list(csv.DictReader(io.StringIO(hledger_command(
    ["balance", "--daily", "--cumulative", "--output-format", "csv"])))
)

daily_transactions = list(csv.DictReader(io.StringIO(hledger_command(
    ["balance", "--daily", "--output-format", "csv"])))
)


timestamps = set(
    map(
        parse_date,
        filter(
            lambda key: key != 'account',
            daily_transactions[0].keys()
        )
    )
).union(
    set(
        map(
            parse_date,
            filter(
                lambda key: key != 'account',
                daily_balances[0].keys()
            )
        )
    )
)

raw_prices = hledger_command(["prices"]).splitlines()

monthly_budgets = list(csv.reader(io.StringIO(hledger_command(
    ["balance", "--budget", "--monthly", "--output-format", "csv"])))
)

metrics = {
    ("hledger_balance", "daily balance for every (account, currency)"): metric_hledger_balance(daily_balances),
    ("hledger_transactions", "daily sum of all transactions for every (account, currency)"): metric_hledger_transactions(daily_transactions),
    ("hledger_fx_rate", "exchange rate from every exsting currency to all target currencies"): metric_hledger_fx_rate(raw_prices, timestamps),
    ("hledger_budget", "monthly budget for (account, currency)"): metric_hledger_budget(monthly_budgets),
}

for metric, values in metrics.items():
    name = metric[0]
    help = metric[1]
    print(f'# TYPE {name} gauge')
    print(f'# HELP {name} {help}')
    for labels_tuples, samples in values.items():
        labels = ','.join(
            map(lambda tuple: f'{tuple[0]}="{tuple[1]}"', labels_tuples))
        for datetime, value in samples:
            timestamp = int(time.mktime(datetime.timetuple()))
            print(
                f'{name}{{{labels}}} {value:.3f} {timestamp}')

print('# EOF')
