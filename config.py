# -*- coding: utf-8 -*-
"""
Created on Tue Mar 14 15:11:08 2023

@author: alevesque
"""

#fee can be reduced by extensive BNB holdings, high monthly trade volume (USD), or 25% discount for paying fees with BNB. see https://www.binance.us/en/fee/schedule
fee = 0.001

TRAIL_PERCENT = 0.02

use_testnet = True
reset_orders = True

if use_testnet:
    #KEYS FOR TESTNET
    API_KEY = ""
    API_SECRET = ""

    #WALLET ASSETS
    balance = {
        'USDT': 0,
        'USD': 0,
        'BNB': 0,
        'LTC': 0,
        'BUSD': 0,
        'VTH': 0,
        'BTC': 0,

    }
else:
    #KEYS FOR REAL SERVER
    API_KEY = ""
    API_SECRET = ""

    #WALLET ASSETS
    balance = {
        'USD': 0,
        'XLM': 0
    }

# order statuses that mean the order is not closed out
ALIVE_ORDER_STATUS = ['NEW', 'PARTIALLY_FILLED']

# acceptable parameters for api create_order() method
API_ORDER_PARAMS = [
    'symbol',
    'side',
    'type',
    'timeInForce',
    'quantity',
    'quoteOrderQty',
    'price',
    'newClientOrderId',
    'icebergQty',
    'newOrderRespType',
    'recvWindow',
    ]

order_opts = {
    'symbol': '',
    'side': 'SELL',
    'type': 'LIMIT', #limit, market, etc
    'timeInForce': 'GTC',
    'quantity': 0.0,
    'price': 0.0,
    # 'newClientOrderId': 0, # optional, autogen by api
    'time': 0,
    'status': 'NEW',
    'commission': 0.0
}


# order types
order_type_buy = 'MARKET'
order_type_sell = 'LIMIT'

# list of pairs to analyze
trading_pairs = {
    'BNBUSDT':  # change back to xlm
        {
            'precision': 4,
            'baseAsset': "BNB",
            'quoteAsset': "USDT"

        },
    'XLMUSD':  # change back to xlm
        {
            'precision': 4,
            'baseAsset': "XLM",
            'quoteAsset': "USD"

        },
    'VTHBTC':  # change back to xlm
        {
            'precision': 4,
            'baseAsset': "VTH",
            'quoteAsset': "BTC"

        },
    'LTCBUSD':  # change back to xlm
        {
            'precision': 4,
            'baseAsset': "LTC",
            'quoteAsset': "BUSD"

        },
}



intervals = ['5m', '15m']
