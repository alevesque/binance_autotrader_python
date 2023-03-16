from binance.exceptions import *
import time
import datetime, requests, asyncio, signal, warnings, config
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
Binance Autotrader - Bollinger Strategy
==============================================================================================

"""


"""
TODO:

[ ]    1. backtest with new data and try to find better algo
            1a. machine learning to correlate indicators with swings over historical data?
            1b. Bollinger bands
[ ] 2. run and test
[X]        2a. implement terminal bell or other notif so can see if good time to buy    
            2a.1. pulseaudio -k   --->  python: print("\a")
[ ] 3. uncomment order lines and actually implement
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
        self.initialize_exchange_connection()
        
        if config.reset_orders:
            open_orders = await self.async_client.get_open_orders()
            await close_all_open_orders(self.async_client, open_orders)
            print('All open orders closed.')
        
        self.order_stop_trail = Order()
        self.order_sell_high = Order()
        self.order_buy = Order()
        
        self.starttime = time.time()
        self.prelim_data_spool()
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
        self.xlmusd_kline_5m = bm.kline_socket(symbol=config.trading_pairs[0]['pair'], interval='5m')
        trade_manager = bm.user_socket()
        print('Sockets initialized.')
        
        #update initial wallet balances
        self.balance = await self.wallet_update()
        if self.balance == -1:
            return -1
        else:
            print(self.balance)
        
        
    def initialize_exchange_connection(self):
        # initialize websocket client to livestream data one ping at a time. preferable to 
        # downloading redundant or late data as would happen with manual update periods using regular client
        self.async_client = await AsyncClient.create(api_key=config.api_key, api_secret=config.api_secret, testnet=config.use_testnet, tld='us')
        self.bm = BinanceSocketManager(self.async_client, user_timeout=5)
    

    def prelim_data_spool(self):
        # download initial data for the longer EMA timeframes, so calculations can start immediately
        # rather than spooling up hundreds of ticks of price data for EMA600, 400 etc
        for trading_pair in config.trading_pairs:
            init_data_raw = await self.async_client.get_klines(symbol=trading_pair['pair'], interval='5m', limit=601)
            data = pd.DataFrame(np.array(init_data_raw)[:,0:6],
                       columns=['open_time', 'open', 'high', 'low', 'close', 'volume'],
                       dtype='float64')
            print('Initial data loaded - {}'.format(trading_pair['pair']))
        
        exchange_info_raw = await self.async_client.get_exchange_info()
        self.exchangeinfo = [pair for pair in exchange_info_raw['symbols'] if pair['symbol'] in [t_p for t_p['pair'] in config.trading_pairs]]
       
        
    def process_raw_klines(self, new_data_raw, data):

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
        data = data.append(new_data, ignore_index=True)
        data = data.drop(0)
        #convert to float64 so talib doesn't bitch
        data = data.astype('float64')
        #note: resulting dataframe is 1-indexed not zero
        return data
    
    
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
        
    def buy_logic(self):
        ######### TODO: INSERT BUY CONDITIONS HERE ##########
        pass

    def sell_logic(self):
        ############# TODO: INSERT CONDITIONS HERE ##############
        pass
    
    
    def trading_strategy(self, data, trading_pair):
        #send data to function to calculate indicators
        #NOTE: testnet only goes back ~170 ticks
        indicators = self.indicator_data(data)
        
        #reset order info template - if no B/S order is warranted, the empty values will
        #notify other functions that there wasn't an order this tick
        # 2023 note: dont want to reset, because need to call api via check_order_status() with last orderId
        # where does it need these values to reset? - under main "#if trading_strategy() decided conditions warrant an order, place order"
        # self.order_buy.reset()
        # self.order_sell_high.reset()
        # self.order_stop_trail.reset()
    
        #shorter variable for asset balance - just so B/S logic is simpler to read
        bal = float(balance[trading_pair[0]['baseAsset']])
    
        buy_type = 'MARKET'
        sell_type = 'MARKET'
        min_position_buy, min_position_sell, max_trade_buy, max_trade_sell = self.set_max_min_trade_qty(buy_type, sell_type)
    
    
        #####################
        # BUYING CONDITIONS #
        #####################
        
        if self.carryover_vars['last_order_sell'] == 1 and bal < min_position_buy: #reset when live to bal<minpos 
            if self.check_order_status(self.order_buy):
                if self.buy_logic():
                    buyQty = float("{:.0f}".format(0.98*float(balance[trading_pair[0]['quoteAsset']])/data['close'].values[-1]))
                    self.carryover_vars['buysize'] = buyQty
                    if buyQty > max_trade_buy:
                        buyQty = max_trade_buy

                    self.carryover_vars['buyprice'] = float("{:.4f}".format(data['close'].values[-1]))
                    self.carryover_vars['buycomm'] = self.carryover_vars['buysize']*self.carryover_vars['buyprice']*self.carryover_vars['COMMRATE']
                    self.carryover_vars['buytime'] = time.time()
                    #===========
                    #BUY ACTIONS
                    #===========
                    self.order_buy.set(
                        **{
                        'symbol': trading_pair[0]['pair'], 
                        'side': 'BUY', 
                        'type': buyType,#'LIMIT', #limit, market, etc
                        'timeInForce': 'GTC',
                        'quantity': buyQty,
                        'price': self.carryover_vars['buyprice'],
                        'orderId': 0,
                        'time': 0,
                        'status': 'test',
                        'commission': self.carryover_vars['buycomm']
                        }
                        )
                    order = self.order_buy
                    
                
                #self.buy(exectype=bt.Order.Limit,price=buyprice,size=buysize,valid=bt.date2num(data.num2date())+(5/1440))
                
                         
        ######################
        # SELLING CONDITIONS #
        ######################
        
        elif self.carryover_vars['last_order_buy'] == 1 and bal >= min_position_sell:  # comment out bal >= minpossell when test
            if self.check_order_status(self.order_sell_high, self.order_stop_trail):
                last_closing_price=float(data['close'].values[-1])
                sellQty = bal  # reset bal when test to self.carryover_vars['buysize']
                if sellQty > max_trade_sell:
                    sellQty = max_trade_sell
    
                if self.sell_logic():
                    #============
                    #SELL ACTIONS
                    #============
                    
                    self.order_sell_high.set(
                        **{
                        'symbol': trading_pair[0]['pair'], 
                        'side': 'SELL',
                        'type': sellType, #limit, market, etc
                        'timeInForce': 'GTC',
                        'quantity': sellQty,
                        'price': last_closing_price,
                        'orderId': 0,
                        'time': 0,
                        'status': 'test',
                        'commission': 0.0
                        }
                        )
                    order = self.order_sell_high
                # otherwise, if more than 8.5 days have passed market prob crashed so dump shares to resume trading
                elif time.time() > (self.carryover_vars['buytime'] + 8.5*24*3600):
                    self.order_stop_trail.set(
                        **{
                            'symbol': trading_pair[0]['pair'], 
                            'side': 'SELL',
                            'type': 'MARKET', #limit, market, etc
                            'timeInForce': 'GTC',
                            'quantity': sellQty,
                            'price': last_closing_price,
                            'orderId': 0,
                            'time': 0,
                            'status': 'test',
                            'commission': 0.0
                            }
                        )
                    order = self.order_stop_trail
        
        return indicators, order
        
    def check_order_status(self, *args):
        orders = (item for item in args if item is not None)
        if orders:
            for order in orders:
                order_details = await self.async_client.get_order(orderId=order.order['orderId'])
                if order_details['status'] not in config.ALIVE_ORDER_STATUS:
                    return False
        return True
    
    def log_data(self, indicators, close_price, order):
        
        dt = datetime.datetime.fromtimestamp(order['time']/1000)
    
        #if an order is currently active, log relevant info to console
        #otherwise log indicators
        if order['status']:
            
            #prevent div by zero error on initial iteration - gain % will be wrong but wgaf
            if self.carryover_vars['buyprice'] == 0.0 or self.carryover_vars['buysize'] == 0.0:
                self.carryover_vars['buysize'] = 1.0
                self.carryover_vars['buyprice'] = 1.0
            
    
            output = (
                        f"OrderID: {order['orderId']:3d} Type: {order['symbol']:<2} {order['type']:<2} " + 
                        f"{order['side']:<2} ".ljust(5) +
                        f"Status: " +
                        f"{order['status']:1} ".ljust(17) +
                        f"Size: ".ljust(6) +
                        f"{order['quantity']:6.2f} ".rjust(8) +
                        f"Price: {order['price']:6.4f} " +
                        f"Cost: {order['quantity']*order['price']:6.2f} " +
                        f"Comm: {order['quantity']*order['price']*self.carryover_vars['COMMRATE']:4.2f} "
                    )                    
            
            if order['status'] == "FILLED":
                if order['side'] == 'SELL':
                    output += (
                                f"gain: {((order['price']-self.carryover_vars['buyprice'])*order['quantity'] - order['quantity']*order['price']*self.carryover_vars['COMMRATE'] - self.carryover_vars['buycomm']):4.2f} " + 
                                f"gain%: {(100*(((order['price']-self.carryover_vars['buyprice'])*self.carryover_vars['buysize'] - order['commission'] - self.carryover_vars['buycomm'])/(self.carryover_vars['buysize']*self.carryover_vars['buyprice']))):3.2f}% "
                                )
                    
                    self.carryover_vars['last_order_sell'] = 1
                    self.carryover_vars['last_order_buy'] = 0
                else:
                    print("\a")
                    
                    self.carryover_vars['last_order_sell'] = 0
                    self.carryover_vars['last_order_buy'] = 1
                f = open("tradelog.txt", "a")
                f.write('%s, %.8s, %s' % (dt.date().isoformat(), dt.time(),output))
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
        #remove excess order params that will cause error if used with MARKET order
        #comment if statement when testing
        if order_parameters['type']=='MARKET':
            del order_parameters['timeInForce']
            del order_parameters['price']
        
        try:
            #for testing only (ie no order actually placed so fake order response):
            """
            order_result_raw = {
                                'symbol': order_parameters['symbol'],
                                'side': order_parameters['side'],
                                'type': order_parameters['type'],
                                'timeInForce': order_parameters['timeInForce'],
                                'executedQty': order_parameters['quantity'],
                                'price': order_parameters['price'],
                                'orderId': 000000,
                                'transactTime': time.time()*1000,
                                'status': 'FILLED',
                                'fills': [{'price': order_parameters['price'], 'qty': order_parameters['quantity'], 'commission': '0.00000000', 'commissionAsset': 'USDT', 'tradeId': 00000}]
                                }
            """
            
            #comment order_result_raw line below when testing
            order_result_raw = await self.async_client.create_order(**order_parameters)
            order_result = self.order_stream_data_process(order_result_raw, datastream='Type1')
            return order_result

        except BinanceAPIException as e:
            print(e)
            print("Error placing order - something went wrong.")
            return -1
        except requests.exceptions.RequestException as e:
            print(e)
            print("Error - Check connection - placing order")
            return -1
        except aiohttp.client_exceptions.ClientOSError as e:
            print(e)
            print('Error - connection reset by peer.')
            return -1

    async def close_all_open_orders(self, open_orders):
        for order in open_orders:
            print(f"Order closed: Symbol: {order['symbol']} OrderId: {order['orderId']}")
            await self.async_client.cancel_order(symbol=order['symbol'], orderId=order['orderId'])
        return

    def order_stream_data_process(self, order_result_raw, datastream):
        if datastream == 'Type1':
            comm = 0
            avg_price = 0
            i = 0
            for x in order_result_raw['fills']:
                comm += float(x['commission'])
                avg_price += float(x['price'])
                i += 1
            if i > 0:
                avg_price = avg_price/i
            order_result = {
                            'symbol': order_result_raw['symbol'],
                            'side': order_result_raw['side'],
                            'type': order_result_raw['type'],
                            'timeInForce': order_result_raw['timeInForce'],
                            'quantity': order_result_raw['executedQty'],
                            'price': avg_price,#order_result_raw['price'],
                            'orderId': order_result_raw['orderId'],
                            'time': order_result_raw['transactTime'],
                            'status': order_result_raw['status'],
                            'commission': comm
                            }

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

        return order_result



    #update wallet values with latest balances
    async def wallet_update(self):
        #get account info for balance checking
        try:
            acc_res = await self.async_client.get_account(recvWindow=10000)
        except BinanceAPIException as e:
            print(e)
            print("checking wallet - something went wrong.")
            return -1
        except requests.exceptions.RequestException as e:
            print(e)
            print("Error - Check connection - checking wallet")
            return -1
        except aiohttp.client_exceptions.ClientOSError as e:
            print(e)
            print('Error - connection reset by peer.')
            return -1


        if 'balances' in acc_res:
            wallet_pair_list = [pair for pair in config.balance]
            for bal in acc_res['balances']:
                if bal['asset'] in wallet_pair_list:
                    config.balance[bal['asset']] = bal['free']     
            return config.balance
        else:
            print('Error - no balances in Account')
            return -1


    def indicator_data(self, data):
        #only load talib when needed
        #TALib_EMA = abstract.EMA
        TALib_BOLL = abstract.BBANDS
        TALib_RSI = abstract.RSI

        #calculate indicators
        RSI6 = TALib_RSI(data['close'].values,timeperiod=6)
        RSI50 = TALib_RSI(data['close'].values,timeperiod=50)
        bband_upper, bband_middle, bband_lower = TALib_BOLL(data['close'],timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=5)

        indicators = {}
        #Index note: indicators[1] == EMAXX[-1] and indicators[0] == EMAXX[-2])
        for i in range(2):  # range(2) because we want both the ultimate and penultimate values
            indicators[i] = { 

                            'RSI6': RSI6[i-2],
                            'RSI50': RSI50[i-2],
                            'bband_upper': bband_upper[i-2],
                            'bband_middle': bband_middle[i-2],
                            'bband_lower': bband_lower[i-2]    
                            
                            }
        return indicators
      

        
async def main(*args):
    autotrader = BinanceTrader()
    while loop_active_bool:
        
        # then start receiving messages
        async with self.xlmusd_kline_5m as xlmusd_kline_5m_message:
            # while time not >20h, otherwise go back to beginning of first while loop to reset connection to avoid server-side disconnect
            while time.time() < (starttime + 20*3600) and loop_active_bool:
                # take in new kline data from websocket stream
                try:
                    new_data_raw = await xlmusd_kline_5m_message.recv()
                except BinanceAPIException as e:
                    print(e)
                    print('Querying kline from stream - something went wrong.')
                    break
                except requests.exceptions.RequestException as e:
                    print(e)
                    print('Error - Check connection - Querying kline from stream')
                    break        
                except aiohttp.client_exceptions.ClientOSError as e:
                    print(e)
                    print('Error - connection reset by peer.')
                    break    
                #process raw data into simpler form
                data_processed = self.process_raw_klines(new_data_raw, data) ####### data needs new name; scope?
                
                #send data to functions that calc indicators, calc strat, buy/sell if necessary, and log to console
                indicators, order = autotrader.trading_strategy(data_processed, config.trading_pairs)
                
           
                #if trading_strategy() decided conditions warrant an order, place order
                if order['side'] != None:
                    
                    async with trade_manager as order_response:
                        print(balance)
                        order = await autotrader.place_order(self.async_client,
                                                             symbol=order['symbol'],
                                                             side=order['side'],
                                                             type=order['type'],
                                                             timeInForce=order['timeInForce'],
                                                             quantity="{0:.{1}f}".format(order['quantity'], config.trading_pairs[0]['precision']),
                                                             price="{:4f}".format(order['price'])
                                                             )
                        if order == -1:
                            break
                        while order['status'] != "FILLED" and order['status'] != "CANCELED" and order['status'] != "EXPIRED":
                            #if it hasnt been more than an hour, try to sell more partial orders
                            if order['time']/1000 < (data['open_time'].values[-1]/1000)+3600:
                                try:
                                    order_result_raw = await order_response.recv()
                                except BinanceAPIException:
                                    print('Socket timeout')
                                    break
                                except aiohttp.client_exceptions.ClientOSError as e:
                                    print(e)
                                    print('Error - connection reset by peer.')
                                    break
                                #if update is from order execution, log response (dont care about other account updates on this stream)
                                if order_result_raw['e'] == 'executionReport':
                                    order = order_stream_data_process(order_result_raw,datastream='Type2')
                                    autotrader.log_data(indicators, data['close'].values[-1], order)
                            
                            #if order open longer than 1h, cancel    
                            else:
                                order = await self.async_client.cancel_order(symbol=order['symbol'],orderId=order['orderId'],recvWindow=10000)
                                order['time'] = data['open_time'].values[-1]
                                order['quantity'] = 0
                                order['commission'] = 0
                        
                        
                        #log data and return updated b/s order status (order b/s complete)
                        log_data(indicators, data['close'].values[-1], order)
                        
                        #update wallet balance
                        balance = await self.wallet_update()
                        if balance == -1:
                            break
                        else:
                            print(balance)
                else:
                    #log data and return updated persistent variables
                    order['time'] = data['open_time'].values[-1]
                    log_data(indicators, data['close'].values[-1], order)
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
