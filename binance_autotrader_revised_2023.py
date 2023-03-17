from binance.exceptions import *
import time
import datetime, requests, asyncio, warnings, config
import numpy as np
import pandas as pd
from talib import abstract
#import json
from binance import AsyncClient, BinanceSocketManager
import aiohttp


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
reconfigure carryover_vars to instance vars

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
        self.initialize_exchange_connection()
        
        if config.reset_orders:
            open_orders = await self.async_client.get_open_orders()
            await self.close_all_open_orders(self.async_client, open_orders)
            print('All open orders closed.')
        
        self.order_stop_trail = config.Order()
        self.order_sell_high = config.Order()
        self.order_buy = config.Order()
        
        self.data = None
        
        self.prelim_data_spool()
        exchange_info_raw = await self.async_client.get_exchange_info()
        self.exchangeinfo = [pair for pair in exchange_info_raw['symbols'] if pair['symbol'] in [t_p['pair'] for t_p in config.trading_pairs]]
        self.min_position_buy, self.min_position_sell, self.max_trade_buy, self.max_trade_sell = self.set_max_min_trade_qty('LIMIT', 'LIMIT')
        
        self.carryover_vars = {  # TODO: replace all with instance variables
        					'prev_max_balance': 0,
        					'current_balance': 0,
        					'buyprice': 0,
        					'buytime': 0,
        					'buycomm': 0,
        					'buysize': 0,
        					'sellprice': 0,
        					'selltime': 0,
        					'COMMRATE': 0.001,
        					'sell_cond_count': [0,0,0,0]
        }
        
        # start any sockets here, i.e. a trade socket
        self.xlmusd_kline_5m = self.bm.kline_socket(symbol=config.trading_pairs[0]['pair'], interval='5m')
        self.trade_manager = self.bm.user_socket()
        print('Sockets initialized.')
        self.balance_update()

    #update wallet values with latest balances
    def balance_update(self):
        #get account info for balance checking
        acc_res = await self.server_connect_try_except('self.async_client.get_account(recvWindow=10000)')
        
        if 'balances' in acc_res:
            wallet_pair_list = [pair for pair in config.balance]
            for bal in acc_res['balances']:
                if bal['asset'] in wallet_pair_list:
                    config.balance[bal['asset']] = float(bal['free'])
            print(config.balance)
            return config.balance
        else:
            print('No balances in Account!')
            return False

    async def initialize_exchange_connection(self):
        # initialize websocket client to livestream data one ping at a time. preferable to 
        # downloading redundant or late data as would happen with manual update periods using regular client
        self.async_client = await AsyncClient.create(api_key=config.api_key, api_secret=config.api_secret, testnet=config.use_testnet, tld='us')
        self.bm = BinanceSocketManager(self.async_client, user_timeout=5)

    async def prelim_data_spool(self):
        # download initial data for the longer EMA timeframes, so calculations can start immediately
        # rather than spooling up hundreds of ticks of price data for EMA600, 400 etc
        for trading_pair in config.trading_pairs:
            init_data_raw = await self.async_client.get_klines(symbol=trading_pair['pair'], interval='5m', limit=601)
            self.data = pd.DataFrame(np.array(init_data_raw)[:,0:6],
                       columns=['open_time', 'open', 'high', 'low', 'close', 'volume'],
                       dtype='float64')
            print('Initial data loaded - {}'.format(trading_pair['pair']))
        
    def process_raw_klines(self, new_data_raw):

        # take only relevant data (OHLC, volume)
        new_data = {
                'open_time': new_data_raw['k']['t'],
                'open': new_data_raw['k']['o'],
                'high': new_data_raw['k']['h'],
                'low': new_data_raw['k']['l'],
                'close': new_data_raw['k']['c'],
                'volume': new_data_raw['k']['v'],
        }
        
        #add new data tick onto existing data set and remove the 0th line to avoid dataset getting huge and
        #overwhelming memory. keep only what is needed for biggest indicator. i.e. 600 lines for EMA600
        self.data.append(new_data, ignore_index=True)
        self.data.drop(0)
        #convert to float64 so talib doesn't bitch
        self.data.astype('float64')
        #note: resulting dataframe is 1-indexed not zero
        return

    def set_max_min_trade_qty(self, buy_type, sell_type):
        
        for filters in self.exchangeinfo[0]['filters']:
            if buy_type == 'MARKET':
                if filters['filterType'] == 'MARKET_LOT_SIZE':
                    min_position_buy = float(filters['minQty'])
                    max_trade_buy = float(filters['maxQty'])
            else:
                if filters['filterType'] == 'LOT_SIZE':
                    min_position_buy = float(filters['minQty'])
                    max_trade_buy = float(filters['maxQty'])
                    
        for filters in self.exchangeinfo[0]['filters']:
            if sell_type == 'MARKET':
                if filters['filterType'] == 'MARKET_LOT_SIZE':
                    min_position_sell = float(filters['minQty'])
                    max_trade_sell = float(filters['maxQty'])
            else:
                if filters['filterType'] == 'LOT_SIZE':
                    min_position_sell = float(filters['minQty'])
                    max_trade_sell = float(filters['maxQty'])
        
        return min_position_buy, min_position_sell, max_trade_buy, max_trade_sell
        
    def buy_logic(self, indicators):
        ######### TODO: INSERT BUY CONDITIONS HERE ##########
        return True

    def sell_logic(self, indicators):
        ############# TODO: INSERT CONDITIONS HERE ##############
        return True

    def trading_strategy(self, trading_pair, indicators):
        #reset order info template - if no B/S order is warranted, the empty values will
        #notify other functions that there wasn't an order this tick
        # 2023 note: dont want to reset, because need to call api via check_order_status() with last orderId
        # where does it need these values to reset? - under main "#if trading_strategy() decided conditions warrant an order, place order"
        # self.order_buy.reset()
        # self.order_sell_high.reset()
        # self.order_stop_trail.reset()
    
        #shorter variable for asset balance - just so B/S logic is simpler to read
        bal = float(self.balance[trading_pair[0]['baseAsset']])
        #####################
        # BUYING CONDITIONS #
        #####################
        
        if self.carryover_vars['last_order_sell'] == 1 and bal < self.min_position_buy: #reset when live to bal<minpos 
            order_opts = {
                'symbol': trading_pair[0]['pair'], 
                'side': 'SELL',
                'type': 'LIMIT', #limit, market, etc
                'timeInForce': 'GTC',
                'quantity': 0.0,
                'price': 0.0,
                # 'newClientOrderId': 0, # optional, autogen by api
                'time': 0,
                'status': 'test',
                'commission': 0.0
                }
            if self.check_order_status(self.order_buy):
                if self.buy_logic(indicators):
                    self.carryover_vars['buysize'] = float("{:.0f}".format(0.98*float(self.balance[trading_pair[0]['quoteAsset']])/self.data['close'].values[-1]))
                    if self.carryover_vars['buysize'] > self.max_trade_buy:
                        self.carryover_vars['buysize'] = self.max_trade_buy

                    self.carryover_vars['buyprice'] = float("{:.4f}".format(self.data['close'].values[-1]))
                    self.carryover_vars['buycomm'] = self.carryover_vars['buysize']*self.carryover_vars['buyprice']*self.carryover_vars['COMMRATE']
                    self.carryover_vars['buytime'] = time.time()
                    #===========
                    #BUY ACTIONS
                    #===========
                    order_opts['side'] = 'BUY'
                    order_opts['quantity'] = self.carryover_vars['buysize']
                    order_opts['price'] = self.carryover_vars['buyprice']
                    order_opts['commission'] = self.carryover_vars['buycomm']
                    self.order_buy.set(**order_opts)
                    order = self.order_buy
                    
        ######################
        # SELLING CONDITIONS #
        ######################
        
        elif self.carryover_vars['last_order_buy'] == 1 and bal >= self.min_position_sell:  # comment out bal >= minpossell when test
            if self.check_order_status(self.order_sell_high, self.order_stop_trail):
                order_opts['price'] = float("{:4f}".format(self.data['close'].values[-1]))
                if bal > self.max_trade_sell:
                    bal = self.max_trade_sell
                # reset bal when test to self.carryover_vars['buysize']
                order_opts['quantity'] = "{0:.{1}f}".format(bal, config.trading_pairs[0]['precision'])
                
                if self.sell_logic(indicators):
                    self.order_sell_high.set(**order_opts)
                    order = self.order_sell_high
                # otherwise, if more than 8.5 days have passed market prob crashed so dump shares to resume trading
                elif time.time() > (self.carryover_vars['buytime'] + 8.5*24*3600):
                    order_opts['type'] = 'MARKET'
                    self.order_stop_trail.set(**order_opts)
                    order = self.order_stop_trail
        
        return order
        
    def check_order_status(self, *args):
        orders = (item for item in args if item is not None)
        if orders:
            for order in orders:
                order_details = await self.async_client.get_order(orderId=order.order['orderId'])
                order.set(**order_details)
                if not order.alive():
                    return False
            return True
        else:
            return False
    
    def log_data(self, indicators, close_price, order_result):
        
        dt = datetime.datetime.fromtimestamp(order_result['time']/1000)
    
        #if an order is currently active, log relevant info to console
        #otherwise log indicators
        if order_result['status']:
            
            #prevent div by zero error on initial iteration - gain % will be wrong but wgaf
            if self.carryover_vars['buyprice'] == 0.0 or self.carryover_vars['buysize'] == 0.0:
                self.carryover_vars['buysize'] = 1.0
                self.carryover_vars['buyprice'] = 1.0
            
    
            output = (
                        f"OrderID: {order_result['orderId']:3d} Type: {order_result['symbol']:<2} {order_result['type']:<2} " + 
                        f"{order_result['side']:<2} ".ljust(5) +
                        "Status: " +
                        f"{order_result['status']:1} ".ljust(17) +
                        "Size: ".ljust(6) +
                        f"{order_result['quantity']:6.2f} ".rjust(8) +
                        f"Price: {order_result['price']:6.4f} " +
                        f"Cost: {order_result['quantity']*order_result['price']:6.2f} " +
                        f"Comm: {order_result['quantity']*order_result['price']*self.carryover_vars['COMMRATE']:4.2f} "
                    )                    
            
            if order_result['status'] == "FILLED":
                if order_result['side'] == 'SELL':
                    output += (
                                f"gain: {((order_result['price']-self.carryover_vars['buyprice'])*order_result['quantity'] - order_result['quantity']*order_result['price']*self.carryover_vars['COMMRATE'] - self.carryover_vars['buycomm']):4.2f} " + 
                                f"gain%: {(100*(((order_result['price']-self.carryover_vars['buyprice'])*self.carryover_vars['buysize'] - order_result['commission'] - self.carryover_vars['buycomm'])/(self.carryover_vars['buysize']*self.carryover_vars['buyprice']))):3.2f}% "
                                )
                    
                    self.carryover_vars['last_order_sell'] = 1
                    self.carryover_vars['last_order_buy'] = 0
                else:
                    print("\a")
                    
                    self.carryover_vars['last_order_sell'] = 0
                    self.carryover_vars['last_order_buy'] = 1
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
    
    async def place_order(self, **order_parameters):
        # remove excess order params that will cause error if used with MARKET order
        # comment if statement when testing
        if order_parameters['type']=='MARKET':
            del order_parameters['timeInForce']
            del order_parameters['price']
        
        # remove extra order params that are useful as part of Order object
        # but are not proper arguments to pass to the api
        for param in order_parameters:
            if param not in config.API_ORDER_PARAMS:
                del order_parameters[param]
        
        
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
        order_result_raw = await self.server_connect_try_except('self.async_client.create_order(**order_parameters)')
        order_result = self.order_stream_data_process(order_result_raw, datastream='Type1')
        return order_result

    async def close_all_open_orders(self, open_orders):
        for order in open_orders:
            print(f"Order closed: Symbol: {order['symbol']} OrderId: {order['orderId']}")
            await self.async_client.cancel_order(symbol=order['symbol'], orderId=order['orderId'])
        return
    
    def server_connect_try_except(self, command: str):
        try:
            conn_result = exec('await ' + command)
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
    
    def order_stream_data_process(self, order_result_raw, datastream):
        order_result = {}
        if order_result_raw:    
            if datastream == 'Type1':
                comm = 0.0
                avg_price = 0.0
                for x in order_result_raw['fills']:
                    comm += float(x['commission'])
                    avg_price += float(x['price'])
                
                i = len(order_result_raw['fills'])
                if i > 0:
                    order_result_raw['price'] /= i
                
                for key in order_result_raw:
                    order_result[key] = order_result_raw.pop(key)
                order_result['quantity'] = order_result.pop('executedQty')
                order_result['time'] = order_result.pop('transactTime')
                order_result['commission'] = comm
                
    
            elif datastream == 'Type2':
                order_result = {
                                'symbol': order_result_raw['s'],
                                'side': order_result_raw['S'],
                                'type': order_result_raw['o'],
                                'timeInForce': order_result_raw['f'],
                                'quantity': order_result_raw['z'],
                                'price': order_result_raw['p'],
                                'orderId': order_result_raw['i'],
                                'time': order_result_raw['E'],
                                'status': order_result_raw['X'],
                                'commission': order_result_raw['n']
                                }
        else:
            order_result = order_result_raw
        self.order.order['status'] = order_result['status']
        return order_result

    def indicator_data(self):
        #calculate indicators
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
      
    async def receive_stream(self, loop_active_bool):
        # start receiving messages
        async with self.xlmusd_kline_5m as xlmusd_kline_5m_message:
            # while time not >20h, otherwise go back to beginning of first while loop to reset connection to avoid server-side disconnect
            # take in new kline data from websocket stream
            new_data_raw = await self.server_connect_try_except(f"{xlmusd_kline_5m_message.recv()}")
            return new_data_raw

async def main(*args):
    
    while loop_active_bool:  
        autotrader = BinanceTrader()
        starttime = time.time()
        while time.time() < (starttime + 20*3600) and loop_active_bool:
            order_result = {}
            # start receiving messages
            new_data_raw = await autotrader.receive_stream(loop_active_bool)

            #process raw data into simpler form and append to data structure
            autotrader.process_raw_klines(new_data_raw)
            
            #send data to function to calculate indicators
            #NOTE: testnet only goes back ~170 ticks
            indicators = autotrader.indicator_data()
            
            #takes data and checks if orders are done, logic true etc and returns order if so
            order = autotrader.trading_strategy(config.trading_pairs, indicators)

            #if trading_strategy() decided conditions warrant an order, place order
            if order.order['side']:

                async with autotrader.trade_manager as order_response:
                    print(autotrader.balance)
                    order_result = await autotrader.place_order(**order.order)
                    if not order_result:
                        break
                    while order.alive():
                        #if it hasnt been more than an hour, try to sell more partial orders
                        if order_result['time']/1000 < (autotrader.data['open_time'].values[-1]/1000)+3600:
                            order_result_raw = await autotrader.server_connect_try_except(f"{order_response.recv()}")
                            
                            #if update is from order execution, log response (dont care about other account updates on this stream)
                            if order_result_raw['e'] == 'executionReport':
                                order_result = autotrader.order_stream_data_process(order_result_raw, datastream='Type2')
                                autotrader.log_data(indicators, autotrader.data['close'].values[-1], order_result)
                        
                        #if order open longer than 1h, cancel    
                        else:
                            order_result = await autotrader.async_client.cancel_order(symbol=order.order['symbol'] ,orderId=order.order['orderId'], recvWindow=10000)
                            order_result['time'] = autotrader.data['open_time'].values[-1]
                            order_result['quantity'] = 0.0
                            order_result['commission'] = 0.0
 
                    #update wallet balance
                    if (autotrader.balance_update() == False):
                        break
            else:
                #log data and return updated persistent variables
                order_result['time'] = autotrader.data['open_time'].values[-1]
                
            #log data and return updated b/s order status (order b/s complete)
            autotrader.log_data(indicators, autotrader.data['close'].values[-1], order_result)
    exit(0)


  

if __name__ == "__main__":

    loop = asyncio.get_event_loop()
    try:
        loop_active_bool = True
        loop.run_until_complete(main(loop_active_bool))
    except KeyboardInterrupt:
        loop_active_bool = False
        loop.run_until_complete(main(loop_active_bool))
    finally:
        print('Program stopped by operator - exiting cleanly.')
