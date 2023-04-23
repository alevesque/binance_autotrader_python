from __future__ import annotations
from binance.exceptions import *
import time
import datetime, requests, asyncio, warnings, sys
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
from signal import SIGINT, SIGTERM

import config
from binance_trader_class_def import BinanceTrader
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
class BinanceTraderWIP(BinanceTrader):
    
    def __init__(self):
        BinanceTrader.__init__(self)
        pass
    
    ##################################################################
    # TRADING STRATEGY (archaic name, change to something more relevant)
    # Determines if it's the right time, then sets up orders with
    # appropriate parameters.
    ##################################################################
    def trading_strategy(self, trading_pair: str, indicators: dict) -> config.Order:
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
        print(f'bal_of_trading_base_asset: {bal_of_trading_base_asset}')
        print(f"trade qty lim buy: {self.trade_qty_limits[trading_pair]['min_position_buy']*0.5}")
        print(f"trade qty lim sell: {self.trade_qty_limits[trading_pair]['min_position_sell']}")
        if bal_of_trading_base_asset <= self.trade_qty_limits[trading_pair]['min_position_buy']*0.5: # TODO delete test multiplier #reset when live to bal<minpos 
            print('Buy cond 1: T')  
            # print(self.order_buy.order)
            if not self.order_buy.alive():# await self.check_order_status(self.order_buy):
                print('buy cond 2: T')
                if self.buy_logic(indicators):
                    print('3')
                    order_opts = self.setup_order_options(
                        side='B',
                        order_type='MARKET',  # TODO del when not in testnet
                        order_name='Buy',
                        **shared_order_vars
                        )
                    print('4')
                    self.order_buy.set(**order_opts)
                    print('buy order set')
                    # print(self.order_buy.order)
            # if we are in buying mode, set return order to order_buy
            # sets to either the existing order or to the newly set options
            order = self.order_buy
                    
        ######################
        # SELLING CONDITIONS #
        ######################
        elif bal_of_trading_base_asset > self.trade_qty_limits[trading_pair]['min_position_sell'] and bal_of_trading_base_asset != 0.0:  # comment out bal >= minpossell when test
            print('sell mode')    
            if not self.order_sell_high.alive():  # await self.check_order_status(self.order_sell_high):
                print('sell high dead')
                if not self.order_stop_trail.alive():  # await self.check_order_status(self.order_stop_trail):
                    print('stop trail dead')
                    # if both sell orders are closed, set trailstop 
                    if time.time() > (self.buy_time + 20):  # TODO: implement trailstop here
                        print(f"stoptrail sell, status: {self.order_stop_trail.order['status']}")    
                        order_opts = self.setup_order_options(
                            order_type='LIMIT',
                            order_name='StopTrail',
                            **shared_order_vars
                            )
                        self.order_stop_trail.set(**order_opts)
                    order = self.order_stop_trail
                else:  # only stoptrail is open
                    # if sell conditions are good, sell for profit
                    print('stop trail alive')
                    if self.sell_logic(indicators):
                        print('sell high')
                        order_opts = self.setup_order_options(
                            order_name='SellHigh',
                            **shared_order_vars)
                        self.order_sell_high.set(**order_opts)
                        order = self.order_sell_high
                        # self.order_stop_trail.reset()
                    
            else:
                # if in sell mode and order_sell_high is open, 
                # return existing order_sell_high order
                order = self.order_sell_high
                pass
        print('done trading strategy')
        return order
    
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
        try:
            #comment order_result_raw line below when testing
            order_result_raw = await self._server_connect_try_except(
                'self.async_client.create_order(**order_p)', 
                {},
                {'order_p': order_parameters, 'self': self}
                )
            if order_result_raw:
                order_result = self._order_stream_data_process(order_result_raw, datastream='Type1')
            else:
                order_result = {}
            return order_result
        except asyncio.CancelledError:
            print('place order cancelled')
            return
       
async def loop_setup():
    autotrader = BinanceTraderWIP()
    init_status = await autotrader._initialize_trade_engine()
    starttime = time.time()
    print('loop has been set up')
    return autotrader, init_status, starttime
    
async def main(*args: bool) -> None:
    
    try:
        # while loop_active_bool:  
        # loop_active_bool = args[0]
        autotrader, init_status, starttime = await loop_setup()
        # while time.time() < (starttime + 20*3600) and loop_active_bool:
        if time.time() < (starttime + 20*3600):  # and loop_active_bool:
            order_result = {}
            last_order_status = None
            # if not init_status:
                # continue
            # start receiving messages
            print('await stream')
            # new_data_raw = await autotrader.receive_stream()
            try:
                await autotrader.receive_stream()
            except asyncio.CancelledError:
                return
            print('received stream')
            # process raw data into simpler form and append to data structure
            # autotrader._process_raw_klines(new_data_raw)
            # send data to function to calculate indicators
            # NOTE: testnet only goes back ~170 ticks
            indicators = autotrader.indicator_data()
            pair_to_trade = 'BNBUSDT'
            #takes data and checks if orders are done, logic true etc and returns order if so
            print('before trade strategy')
            order = autotrader.trading_strategy(pair_to_trade, indicators)
            if order.order['name'] == 'SellHigh':
                if autotrader.order_stop_trail.alive():  
                    a = await autotrader._server_connect_try_except(
                        "self.async_client.cancel_order(" +
                        "symbol=self.order_stop_trail.order['symbol']," +
                        "order_Id=self.order_stop_trail.order['orderId'])",
                        {},
                        {'self': autotrader}
                        )
                    print(f'stoptrail canceled: {a}')
            print('b')
            #if trading_strategy() decided conditions warrant an order, place order
            if order.order['side'] in ['BUY', 'SELL']:
                print('a')
                async with autotrader.trade_manager as order_response:
                    print(autotrader.balance)
                    # print(order.order)
                    order_result = await autotrader.place_order(**order.order)
                    print('c')
                    if order_result:
                        order.order['status'] = order_result['status']
                        print('d')
                    else:
                        print('Error placing order.')
                        # continue
                        
                    while order.alive():
                        print('e')
                        if order_result['status'] != last_order_status:
                            print('f')
                            autotrader.log_data(indicators, autotrader.data['close'].values[-1], order_result)
                        # if it hasnt been more than an hour, try to sell more partial orders
                        if order_result['time']/1000 < (autotrader.data['open_time'].values[-1]/1000)+3600:
                            print('order alive')
                            order_result_raw = await autotrader._server_connect_try_except('a', {}, {'a': order_response.recv()})
                            
                            #if update is from order execution, log response (dont care about other account updates on this stream)
                            if order_result_raw['e'] == 'executionReport':
                                order_result = autotrader._order_stream_data_process(order_result_raw, datastream='Type2')
                                if order_result:
                                    order.order['status'] = order_result['status']
                                else:
                                    continue
                            last_order_status = order_result['status']
                            print(last_order_status)
                        #if order open longer than 1h, cancel    
                        else:
                            order_result = await autotrader.async_client.cancel_order(symbol=order.order['symbol'] ,orderId=order.order['orderId'], recvWindow=10000)
                            order_result['time'] = autotrader.data['open_time'].values[-1]
                            order_result['quantity'] = 0.0
                            order_result['commission'] = 0.0
 
                    #update wallet balance
                    if (await autotrader._balance_update() == False):
                        print('balance update false')
                        # break
            else:
                #log data and return updated persistent variables
                order_result['time'] = autotrader.data['open_time'].values[-1]
                order_result['status'] = None
                
            #log data and return updated b/s order status (order b/s complete)
            print('g')
            autotrader.log_data(indicators, autotrader.data['close'].values[-1], order_result)
        else:
            autotrader, init_status, starttime = await loop_setup()
        return
    except asyncio.CancelledError:
        print('Program cancelled.')
        b = await autotrader.async_client.close_connection()
        print(b)
        # print('loop close')
        # loop.close()
        # print('sys.exit(0)')
        # sys.exit(0)
        return
    
  

if __name__ == "__main__":
    loop_active_bool = True
    nest_asyncio.apply()    
    loop = asyncio.get_event_loop()
    # main_task = asyncio.ensure_future(main(loop_active_bool))
    main_task = loop.create_task(main(loop_active_bool))
    # for signal in [SIGINT, SIGTERM]:
    #    loop.add_signal_handler(signal, main_task.cancel)
    try:
        
        # asyncio.run(main(loop_active_bool))
        loop.run_forever()
    except KeyboardInterrupt:
        print('Keyboard Interrupt')
        loop_active_bool = False
        print('stop loop')
        loop.stop()
        # loop.create_task(main(loop_active_bool))
        # loop.run_until_complete(main(loop_active_bool))
        # asyncio.run(main(loop_active_bool))
    #except RuntimeError:
    #    print('Runtime error')
    finally:
        print('Find all running tasks')
        pending = asyncio.all_tasks(loop=loop)
        for task in pending:
            print(task.get_name())
            task.cancel()
        
        # print('gather pending tasks')
        # group = asyncio.gather(*pending, return_exceptions=True)
        # loop.run_until_complete(group)
        # print('shutdown asyncgens')
        # loop.run_until_complete(loop.shutdown_asyncgens())
        
        if not loop.is_running():
            print('close loop')
            loop.close()
        else:
            print('loop not stopped wtf')
        
   
        print('Program stopped - exiting.')
