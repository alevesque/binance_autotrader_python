from __future__ import annotations
from binance.exceptions import *
import time
import datetime, requests, asyncio, warnings, config, sys
import numpy as np
import pandas as pd
from talib import abstract
# import json
from binance import AsyncClient, BinanceSocketManager
import aiohttp
import nest_asyncio
# import IPython
import copy
with warnings.catch_warnings():
    warnings.simplefilter('ignore', RuntimeWarning)


"""
==============================================================================================
Binance Autotrader - Generic Framework
==============================================================================================
"""

"""
TODO:

check log_data
change dataframe .append to concat() 
move logging, plotting functions in plot_strat_over_time to config to clean things up
same with functions in optimization

[ ] 1. run and test
[X]        2a. implement terminal bell or other notif so can see if good time to buy    
            2a.1. pulseaudio -k   --->  python: print("\a")
[ ] 2. uncomment order lines and actually implement
"""

'''
[ ]    possible upgrade - actively scan several pairs for buy event trigger, buy, sell after certain % gain or other condition
[ ]    captures the daily +15% swings of random currencies on binance "top gainers"

    multi-logging could look like:

    PAIR1 ----
    Pair2 ----
    Pair3 ----

    ===========

    Pair1 ----
    Pair2 ----
    Pair3 ----

'''


class BinanceTrader():
    
    def __init__(self):
        
        self.exchangeinfo = []
        self.async_client = None
        self.bm = None
        self.trade_manager = None
        
        self.order_stop_trail = config.Order()
        self.order_sell_high = config.Order()
        self.order_buy = config.Order()
        
        self.data = None
        
        self.COMM_RATE = 0.001
        self.buy_price = 0
        self.buy_time = 0
        self.buy_comm = 0
        self.buy_size = 0
        pass
        
    
    async def get_exchange_info(self):
        exchange_info_raw = await self.async_client.get_exchange_info()
        # TODO TRY: self.exchangeinfo is dict of dicts, each containing exchange information about a trading pair listed in config
        assets = [t_p for t_p in config.trading_pairs]
        asset_details = [p for p in exchange_info_raw['symbols']
                         if p['symbol'] in assets]
        self.exchangeinfo = {pair:symbol for pair, symbol in 
                        zip(
                            [s['symbol'] for s in asset_details],
                            [p for p in exchange_info_raw['symbols']
                             if p['symbol'] in assets
                             ]
                            )}
   
        # self.exchangeinfo is list of dicts, each containing exchange information about a trading pair listed in config
        # self.exchangeinfo = [pair for pair in exchange_info_raw['symbols'] if pair['symbol'] in [t_p['pair'] for t_p in config.trading_pairs]]
        # TODO TRY:
        self._set_max_min_trade_qty('LIMIT', 'LIMIT')
        return
    
    #update wallet values with latest balances
    async def balance_update(self) -> bool:
        ''' sets self.balance to dict of form:
            {
                'BNB': 1000.00,
                'XLM': 900000.00,
                }'''
        #get account info for balance checking
        acc_result = await self.server_connect_try_except('self.async_client.get_account(recvWindow=10000)')
        # print(acc_result)
        if 'balances' in acc_result:
            wallet_pair_list = [pair for pair in config.balance]
            for bal in acc_result['balances']:
                if bal['asset'] in wallet_pair_list:
                    self.balance[bal['asset']] = float(bal['free'])
            return True # self.balance
        else:
            print('No balances in Account!')
            return False
        
    async def _initialize_exchange_connection(self) -> None:
        # initialize websocket client to livestream data one ping at a time. preferable to 
        # downloading redundant or late data as would happen with manual update periods using regular client
        self.async_client = await AsyncClient.create(api_key=config.API_KEY, api_secret=config.API_SECRET, testnet=config.use_testnet, tld='us')
        self.bm = BinanceSocketManager(self.async_client, user_timeout=5)
        if self.async_client and self.bm:
            print('Exchange connection and websocket manager initialized.')

    async def _prelim_data_spool(self) -> bool:
        # download initial data for the longer EMA timeframes, so calculations can start immediately
        # rather than spooling up hundreds of ticks of price data for EMA600, 400 etc
        for trading_pair in self.exchangeinfo:
            init_data_raw = await self.async_client.get_klines(symbol=trading_pair, interval='5m', limit=601)
            self.data = pd.DataFrame(np.array(init_data_raw)[:,0:6],
                       columns=['open_time', 'open', 'high', 'low', 'close', 'volume'],
                       dtype='float64')
            print('Initial data loaded - {}'.format(trading_pair))
        return True
    
    ##################################################################
    # PROCESS RAW KLINES
    # Appends relevant information to self.data and removes first entry
    # to avoid overloading memory (only take what we need from past data)
    ##################################################################
    def process_raw_klines(self, new_data_raw: dict) -> None:

        # take only relevant data (OHLC, volume)
        # convert to float64 so talib doesn't bitch
        new_data = pd.DataFrame([{
            'open_time': new_data_raw['k']['t'],
            'open': new_data_raw['k']['o'],
            'high': new_data_raw['k']['h'],
            'low': new_data_raw['k']['l'],
            'close': new_data_raw['k']['c'],
            'volume': new_data_raw['k']['v'],
        }]).astype('float64')
        
        #add new data tick onto existing data set and remove the 0th line
        # to avoid dataset getting huge and overwhelming memory. keep only 
        # what is needed for biggest indicator. i.e. 600 lines for EMA600
        self.data = pd.concat([self.data, new_data], ignore_index=True)
        self.data.drop([0], inplace=True)
        # print('self.data from self.process_raw_klines():')
        # IPython.display.display(self.data)
        return

    def _set_max_min_trade_qty(self, buy_type: str, sell_type: str) -> None:
        self.trade_qty_limits = {}
        buy_type_filter = 'MARKET_LOT_SIZE' if buy_type == 'MARKET' else 'LOT_SIZE'
        # creates list of dicts with the trading pair buy/sell limits
        # filters out trading pairs not listed in config.py
        ''' i.e.
        [{'max_trade_buy': '100000.00000000',
          'max_trade_sell': '100000.00000000',
          'min_position_buy': '0.00100000',
          'min_position_sell': '0.00100000'},
         {'max_trade_buy': '200000.00000000',
          'max_trade_sell': '200000.00000000',
          'min_position_buy': '0.00200000',
          'min_position_sell': '0.00200000'}]
        '''
        trade_qty_limit_values = [
            {
                'min_position_buy': float(filters['minQty']),
                'min_position_sell': float(filters['minQty']),
                'max_trade_buy': float(filters['maxQty']), 
                'max_trade_sell': float(filters['maxQty'])
                } 
            for asset in self.exchangeinfo 
            for filters in self.exchangeinfo[asset]['filters']
            if buy_type_filter in filters['filterType']
            ]
        # create dict with trading pairs as keys and the applicable 
        # trade_qty_limit_values item as values
        ''' i.e. 
        {'BNBUSDT': {'max_trade_buy': '100000.00000000',
                     'max_trade_sell': '100000.00000000',
                     'min_position_buy': '0.00100000',
                     'min_position_sell': '0.00100000'},
         'VTHBTC': {'max_trade_buy': '200000.00000000',
                    'max_trade_sell': '200000.00000000',
                    'min_position_buy': '0.00200000',
                    'min_position_sell': '0.00200000'}}}
        '''
        self.trade_qty_limits = {
            pair:limits for pair, limits in 
            zip(
                [t_p for t_p in self.exchangeinfo],
                [trade_qty_limit_values[i] for i in range(len(self.exchangeinfo))]
                )
            }
        pass
    ##################################################################
    # BUY LOGIC
    # Takes indicators and returns True/False if conditions are met or not
    ##################################################################
    def buy_logic(self, indicators: dict) -> bool:
        ######### TODO: INSERT BUY CONDITIONS HERE ##########
        return True
    
    ##################################################################
    # SELL LOGIC
    # Takes indicators and returns True/False if conditions are met or not
    ##################################################################
    def sell_logic(self, indicators: dict) -> bool:
        ############# TODO: INSERT CONDITIONS HERE ##############
        return True
    
    def setup_order_options(self, pair: str, base_asset_bal: float, quote_asset_bal: float, side: str='S', order_type: str='LIMIT') -> dict:
        order_options = copy.deepcopy(config.order_opts)
        self._set_max_min_trade_qty(order_type, order_type)
        order_options['symbol'] = pair
        order_options['price'] = float("{:.4f}".format(self.data['close'].values[-1]))
        if side == 'B':
            order_options['side'] = 'BUY'
            self.buy_price = order_options['price']
            
            self.buy_size = 10  # TODO reset when not in testnet # float("{:.0f}".format(0.98*quote_asset_bal/self.data['close'].values[-1]))
            if self.buy_size > self.trade_qty_limits[pair]['max_trade_buy']:
                self.buy_size = self.trade_qty_limits[pair]['max_trade_buy']
            order_options['quantity'] = self.buy_size
            
            self.buy_comm = self.buy_size*self.buy_price*self.COMM_RATE
            order_options['commission'] = self.buy_comm
            self.buy_time = time.time()
           
        elif side == 'S':
            print('order_opts:')
            print(config.order_opts)
            if base_asset_bal > self.trade_qty_limits[pair]['max_trade_sell']:
                base_asset_bal = self.trade_qty_limits[pair]['max_trade_sell']
            # reset bal when test to self.buy_size
            order_options['quantity'] = "{0:.{1}f}".format(base_asset_bal, self.exchangeinfo[pair]['baseAssetPrecision'])
            print(f"self.setup_order_options() sell qty: {order_options['quantity']}")
            order_options['type'] = order_type
        
        return order_options
            
    ##################################################################
    # TRADING STRATEGY (archaic name, change to something more relevant)
    # Determines if it's the right time, then sets up orders with
    # appropriate parameters.
    ##################################################################
    async def trading_strategy(self, trading_pair: str, indicators: dict) -> config.Order:
        bal_of_trading_base_asset = self.balance[config.trading_pairs[trading_pair]['baseAsset']]
        bal_of_trading_quote_asset = self.balance[config.trading_pairs[trading_pair]['quoteAsset']]
        shared_order_vars = {
            'pair': trading_pair,
            'base_asset_bal': bal_of_trading_base_asset,
            'quote_asset_bal': bal_of_trading_quote_asset
            }
        order = config.Order()
        #####################
        # BUYING CONDITIONS #
        #####################
        
        if bal_of_trading_base_asset < self.trade_qty_limits[trading_pair]['min_position_buy']*0.5: # TODO delete test multiplier #reset when live to bal<minpos 
            if not self.order_buy.alive():# await self.check_order_status(self.order_buy):
                if self.buy_logic(indicators):
                    order_opts = self.setup_order_options(
                        side='B',
                        order_type='MARKET',  # TODO del when not in testnet
                        **shared_order_vars
                        )
                    self.order_buy.set(**order_opts)
            # if we are in buying mode, set return order to order_buy
            # sets to either the existing order or to the newly set options
            order = self.order_buy
                    
        ######################
        # SELLING CONDITIONS #
        ######################
        
        elif bal_of_trading_base_asset >= self.trade_qty_limits[trading_pair]['min_position_sell'] and bal_of_trading_base_asset != 0.0:  # comment out bal >= minpossell when test
            if not self.order_sell_high.alive():  # await self.check_order_status(self.order_sell_high):
                if not self.order_stop_trail.alive():  # await self.check_order_status(self.order_stop_trail):
                    # if both sell orders are closed, set trailstop 
                    if time.time() > (self.buy_time + 20):  # TODO: implement trailstop here
                        print(f"stoptrail sell, status: {self.order_stop_trail.order['status']}")    
                        order_opts = self.setup_order_options(
                            order_type='MARKET',
                            **shared_order_vars
                            )
                        self.order_stop_trail.set(**order_opts)
                    order = self.order_stop_trail
                else:  # only stoptrail is open
                    # if sell conditions are good, sell for profit
                    if self.sell_logic(indicators):
                        print('sell high')
                        order_opts = self.setup_order_options(**shared_order_vars)
                        self.order_sell_high.set(**order_opts)
                        if self.order_stop_trail.alive():  
                            a = await self.server_connect_try_except(
                                "self.async_client.cancel_order(" +
                                "symbol=self.order_stop_trail.order['symbol']," +
                                "order_Id=self.order_stop_trail.order['orderId'])",
                                {},
                                {'self': self}
                                )
                            if(a):
                                print('stoptrail canceled')
                            
                        '''await self.async_client.cancel_order(
                            symbol=self.order_stop_trail.order['symbol'],
                            order_Id=self.order_stop_trail.order['orderId']
                            )'''
                        order = self.order_sell_high
                        # self.order_stop_trail.reset()
                    
            else:
                # if in sell mode and order_sell_high is open, 
                # return existing order_sell_high order
                order = self.order_sell_high
                pass
        
        
        return order
        
    async def check_order_status(self, *args: config.Order) -> bool:
        orders = (item for item in args if item.order['symbol'] is not None)
        for order in orders:
            if not order.alive():
                return False
            order_details = await self.server_connect_try_except(
                "self.async_client.get_order(" +
                "symbol=order.order['symbol']," +
                "orderId=order.order['orderId'])",
                {},
                {'order': order, 'self': self}
                )
            if order_details:
                order.set(**order_details)
            else:
                print(f"Error querying order: {order.order['orderId']}")
                return False
        return True
    
    def log_data(self, indicators, close_price, order_result) -> bool:
        
        dt = datetime.datetime.fromtimestamp(order_result['time']/1000)
    
        # if an order is currently active, log relevant info to console
        # otherwise log indicators
        if order_result['status']:
            
            #prevent div by zero error on initial iteration - gain % will be wrong but wgaf
            if self.buy_price == 0.0 or self.buy_size == 0.0:
                self.buy_size = 1.0
                self.buy_price = 1.0
    
            output = (
                        f"OrderID: {order_result['orderId']:3d} Type: {order_result['symbol']:<2} {order_result['type']:<2} " + 
                        f"{order_result['side']:<2} ".ljust(5) +
                        "Status: " +
                        f"{order_result['status']:1} ".ljust(17) +
                        "Size: ".ljust(6) +
                        f"{order_result['quantity']:6.2f} ".rjust(8) +
                        f"Price: {order_result['price']:6.4f} " +
                        f"Cost: {order_result['quantity']*order_result['price']:6.2f} " +
                        f"Comm: {order_result['quantity']*order_result['price']*self.COMM_RATE:4.2f} "
                    )                    
            
            if order_result['status'] == "FILLED":
                if order_result['side'] == 'SELL':
                    output += (
                                f"gain: {((order_result['price']-self.buy_price)*order_result['quantity'] - order_result['quantity']*order_result['price']*self.COMM_RATE - self.buy_comm):4.2f} " + 
                                f"gain%: {(100*(((order_result['price']-self.buy_price)*self.buy_size - order_result['commission'] - self.buy_comm)/(self.buy_size*self.buy_price))):3.2f}% "
                                )
                    
                    # self.carryover_vars['last_order_sell'] = 1
                    # self.carryover_vars['last_order_buy'] = 0
                else:
                    print("\a")
                    
                    # self.carryover_vars['last_order_sell'] = 0
                    # self.carryover_vars['last_order_buy'] = 1
                f = open("tradelog.txt", "a")
                f.write('%s, %.8s, %s' % (dt.date().isoformat(), dt.time(), output))
                f.close()
            
        else:
            output = (    
                        f"RSI6: {indicators[1]['RSI6']:7.4f} " +
                        f"RSI50: {indicators[1]['RSI50']:7.4f} " +
                        f"BB-Upper: {indicators[1]['bband_upper']:6.4f} " +
                        f"BB-Middle: {indicators[1]['bband_middle']:6.4f} " +
                        f"BB-Lower: {indicators[1]['bband_lower']:6.4f} " +
                        f"Close Price: {close_price:6.4f} "
                        )
        print('%s, %.8s, %s' % (dt.date().isoformat(), dt.time(), output))
    
        return
    
    ##################################################################
    # PLACE ORDER
    # Formats order params for api, then calls api create_order() method.
    # Returns server response as order_result.
    ##################################################################
    async def place_order(self, **order_params: dict) -> dict:
        # remove excess order params that will cause error if used with MARKET order
        # comment if statement when testing
        if order_params['type']=='MARKET':
            del order_params['timeInForce']
            del order_params['price']
        
        # remove extra order params that are useful as part of Order object
        # but are not proper arguments to pass to the api
        order_parameters = {key:value for key, value in order_params.items() if key in config.API_ORDER_PARAMS}
        
        #for testing only (ie no order actually placed so fake order response):
        """
        order_result_raw = {
                            'symbol': order_parameters['symbol'],
                            'side': order_parameters['side'],
                            'type': order_parameters['type'],
                            'timeInForce': order_parameters['timeInForce'],
                            'executedQty': order_parameters['quantity'],
                            'price': order_parameters['price'],
                            'newClientOrderId': 000000,
                            'transactTime': time.time()*1000,
                            'status': 'FILLED',
                            'fills': [{'price': order_parameters['price'], 'qty': order_parameters['quantity'], 'commission': '0.00000000', 'commissionAsset': 'USDT', 'tradeId': 00000}]
                            }
        """
        
        #comment order_result_raw line below when testing
        order_result_raw = await self.server_connect_try_except('self.async_client.create_order(**order_p)', {}, {'order_p': order_parameters, 'self': self})
        if order_result_raw:
            order_result = self.order_stream_data_process(order_result_raw, datastream='Type1')
        else:
            order_result = {}
        return order_result

    async def close_all_open_orders(self) -> bool:
        open_orders = await self.async_client.get_open_orders()
        if open_orders:
            for order in open_orders:
                '''await self.server_connect_try_except(
                    "self.async_client.cancel_order(symbol=order['symbol'], orderId=order['orderId'])",
                    {},
                    {'order': order, 'self': self})'''
                await self.async_client.cancel_order(symbol=order['symbol'], orderId=order['orderId'])
                print(f"Order closed: Symbol: {order['symbol']} OrderId: {order['orderId']}")
        
        return True
    
    async def server_connect_try_except(self, *args):#command: str):
        try:
            conn_result = await eval(*args)
        except BinanceAPIException as e:
            print(e)
            print('Error querying server - something went wrong.')
            return False
        except requests.exceptions.RequestException as e:
            print(e)
            print('Error - Check connection')
            return False
        except aiohttp.client_exceptions.ClientOSError as e:
            print(e)
            print('Error - connection reset by peer.')
            return False
        else:
            return conn_result
    
    def order_stream_data_process(self, order_result_raw: dict, datastream: str) -> dict:
        order_result = {}
        if order_result_raw:    
            if datastream == 'Type1':
                comm = 0.0
                avg_price = 0.0
                for x in order_result_raw['fills']:
                    comm += float(x['commission'])
                    avg_price += float(x['price'])
                
                i = len(order_result_raw['fills'])
                if i != 0:
                    order_result_raw['price'] = float(order_result_raw['price']) / i
                
                # for key in order_result_raw:
                #     order_result[key] = order_result_raw.pop(key)
                order_result = order_result_raw  # {key:value for key, value in order_result_raw}
                order_result['quantity'] = float(order_result.pop('executedQty'))
                order_result['time'] = float(order_result.pop('transactTime'))
                order_result['commission'] = comm
                
    
            elif datastream == 'Type2':
                order_result = {
                                'symbol': order_result_raw['s'],
                                'side': order_result_raw['S'],
                                'type': order_result_raw['o'],
                                'timeInForce': order_result_raw['f'],
                                'quantity': float(order_result_raw['z']),
                                'price': float(order_result_raw['p']),
                                'orderId': order_result_raw['i'],
                                'time': float(order_result_raw['E']),
                                'status': order_result_raw['X'],
                                'commission': float(order_result_raw['n'])
                                }
        else:
            order_result = order_result_raw
        return order_result
    
    ###################################################################
    # INDICATOR DATA
    # Calculates indicators from the latest self.data and returns the
    # nth and (n-1)th indicators
    ##################################################################
    def indicator_data(self) -> dict:
        #calculate indicators
        # print(self.data['close'])
        RSI6 = abstract.RSI(self.data['close'].values,timeperiod=6)
        RSI50 = abstract.RSI(self.data['close'].values,timeperiod=50)
        bband_upper, bband_middle, bband_lower = abstract.BBANDS(self.data['close'],timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=5)

        indicators = {}
        # Index note: indicators[1] == RSIXX[-1] and indicators[0] == RSIXX[-2]
        # range(2) because we want both the ultimate and penultimate values
        for i in range(2):  
            indicators[i] = { 

                            'RSI6': RSI6[i-2],
                            'RSI50': RSI50[i-2],
                            'bband_upper': bband_upper[i-2],
                            'bband_middle': bband_middle[i-2],
                            'bband_lower': bband_lower[i-2]    
                            
                            }
        return indicators
    
     ##################################################################
     # RECEIVE STREAM
     # Receives latest kline from API
     ##################################################################
    async def receive_stream(self, loop_active_bool: bool) -> dict:
        # start receiving messages
        async with self.xlmusd_kline_5m as xlmusd_kline_5m_message:
            # while time not >20h, otherwise go back to beginning of first while loop to reset connection to avoid server-side disconnect
            # take in new kline data from websocket stream
            new_data_raw = await self.server_connect_try_except("obj", {}, {"obj": xlmusd_kline_5m_message.recv()})
            return new_data_raw

    async def initialize_trade_engine(self) -> bool:
        await self._initialize_exchange_connection()
        if config.reset_orders:
            if await self.close_all_open_orders():
                print('All open orders closed.')
        
        await self.get_exchange_info()
        await self._prelim_data_spool()
        # start any sockets here, i.e. a trade socket
        self.xlmusd_kline_5m = self.bm.kline_socket(symbol=config.trading_pairs['XLMUSD']['pair'], interval='5m')
        self.trade_manager = self.bm.user_socket()
        print('Sockets initialized.')
        self.balance = {}
        await self.balance_update()
        print(self.balance)
        print('Initialization Complete.')
        return True
    
async def main(*args: bool) -> None:
    loop_active_bool = args[0]
    while loop_active_bool:  
        autotrader = BinanceTrader()
        init_status = await autotrader.initialize_trade_engine()
        starttime = time.time()
        while time.time() < (starttime + 20*3600) and loop_active_bool:
            order_result = {}
            # start receiving messages
            new_data_raw = await autotrader.receive_stream(loop_active_bool)
            if not init_status:
                continue
            # process raw data into simpler form and append to data structure
            autotrader.process_raw_klines(new_data_raw)
            # send data to function to calculate indicators
            # NOTE: testnet only goes back ~170 ticks
            indicators = autotrader.indicator_data()
            pair_to_trade = 'BNBUSDT'
            #takes data and checks if orders are done, logic true etc and returns order if so
            order = await autotrader.trading_strategy(pair_to_trade, indicators)
            #if trading_strategy() decided conditions warrant an order, place order
            if order.order['side']:
                async with autotrader.trade_manager as order_response:
                    # print(autotrader.balance)
                    # print(order.order)
                    order_result = await autotrader.place_order(**order.order)
                    
                    if order_result:
                        order.order['status'] = order_result['status']
                    else:
                        print('Error placing order.')
                        continue
                        
                    
                    '''if not order_result:
                        break'''
                    while order.alive():
                        # if it hasnt been more than an hour, try to sell more partial orders
                        if order_result['time']/1000 < (autotrader.data['open_time'].values[-1]/1000)+3600:
                            order_result_raw = await autotrader.server_connect_try_except('a', {}, {'a': order_response.recv()})
                            
                            #if update is from order execution, log response (dont care about other account updates on this stream)
                            if order_result_raw['e'] == 'executionReport':
                                order_result = autotrader.order_stream_data_process(order_result_raw, datastream='Type2')
                                if order_result:
                                    order.order['status'] = order_result['status']
                                else:
                                    continue
                                autotrader.log_data(indicators, autotrader.data['close'].values[-1], order_result)
                        
                        #if order open longer than 1h, cancel    
                        else:
                            order_result = await autotrader.async_client.cancel_order(symbol=order.order['symbol'] ,orderId=order.order['orderId'], recvWindow=10000)
                            order_result['time'] = autotrader.data['open_time'].values[-1]
                            order_result['quantity'] = 0.0
                            order_result['commission'] = 0.0
 
                    #update wallet balance
                    if (await autotrader.balance_update() == False):
                        print('balance update false')
                        break
            else:
                #log data and return updated persistent variables
                order_result['time'] = autotrader.data['open_time'].values[-1]
                order_result['status'] = None
                
            #log data and return updated b/s order status (order b/s complete)
            # autotrader.log_data(indicators, autotrader.data['close'].values[-1], order_result)
        await autotrader.async_client.close_connection()
    sys.exit(0)
  

if __name__ == "__main__":

    nest_asyncio.apply()    
    loop = asyncio.get_event_loop()
    
    try:
        loop_active_bool = True
        # loop.create_task(main(loop_active_bool))
        asyncio.run(main(loop_active_bool))
        # loop.run_until_complete(main(loop_active_bool))
    except KeyboardInterrupt:
        print('Keyboard Interrupt')
        loop_active_bool = False
        # loop.create_task(main(loop_active_bool))
        # loop.run_until_complete(main(loop_active_bool))
        # asyncio.run(main(loop_active_bool))
    #except RuntimeError:
    #    print('Runtime error')
    finally:
        print('Program stopped - exiting.')
