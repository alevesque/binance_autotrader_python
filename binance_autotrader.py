from binance.client import Client
#from binance.enums import *
import time

api_key = "3dUerAA0KFYj9BPhLajkRdx3yxcxgSWqyayemilGsFft4lmyXc9iqyATO5D5xVDX"
api_secret = "8CgimgMn9JFajxCxfO49bAz1Sx8yGSxUuoCyg3Mix1i7SQlzXzuvBiWnVFBcBXAC"

"""fee can be reduced by extensive BNB holdings, high monthly trade volume (USD), or 25% discount for paying fees with BNB. see https://www.binance.us/en/fee/schedule"""
fee = 0.001

"""factor of safety for trades ensures only trades with x% profit get executed, to help prevent loss"""
factor_of_safety=1.005

"""tld='' is used to denote which server to use (.com or .us)"""
client = Client(api_key, api_secret,tld='us')

def place_order(pair, side, order_type, time_in_force,qty,price):
	order = client.create_order(
	    symbol=pair,
	    side=side,
	    type=order_type,
	    timeInForce=time_in_force,
	    quantity=qty,
	    price=price)
	return order

def main():
	

	while 1:
		"""returns dict variable of price and time interval"""
		XTZ2USD_avg_price = client.get_avg_price(symbol='XTZUSD')
		XTZ2BTC_avg_price = client.get_avg_price(symbol='XTZBTC')
		BTC2USD_avg_price = client.get_avg_price(symbol='BTCBUSD')
	

		"""
		if time divisible by 5 (every 5 min) check BNB price in USD, alt, BTC to update fee calcs
		fee for eq1 alt2BTC conversion becomes fee*BNB/alt*USD/BNB
		fee_alt = 0.00075 (BNB) * alt2BNB (alt/BNB)
		"""
		"""
		Equations to calculate if there is profit to be made on exchange rate arbitration. 
		Equation 1 is going from USD to altcoin to BTC to USD.
		Equation 2 is going from USD to BTC to altcoin to USD.
		"""
		
		if (float(XTZ2USD_avg_price["price"])*(1+fee)*factor_of_safety < float(XTZ2BTC_avg_price["price"])*(1-fee)*float(BTC2USD_avg_price["price"])*(1-fee)):
			print("XTZ/BTC - EQ1 TRUE")
			verif_str1 = "[1] USD per XTZ: {}\n[2] BTC per XTZ: {}\n[3] USD per BTC: {}\n\n[1] USD + 0.001*[1] (0.1% fee) buys 1 XTZ -> 1 XTZ - 0.001 XTZ (0.1% fee) buys 0.999*[2] BTC -> 0.999*[2] BTC - 0.001*0.999*[2] BTC (0.1% fee) sells for (0.999^2)*[3] USD.\n"
			print(verif_str1.format(XTZ2USD_avg_price["price"],XTZ2BTC_avg_price["price"],BTC2USD_avg_price["price"]))
			print('\a')
			
		#	usd_balance = client.get_asset_balance(asset='USD')
		#	USD_to_XTZ_order = place_order('XTZUSD','BUY','LIMIT','IOC',"{:.2f}".format(0.002*float(usd_balance["free"])/float(XTZ2USD_avg_price["price"])),"{:.4f}".format(1.0025*float(XTZ2USD_avg_price["price"])))
		#	print('Order for {:.2f} XTZ at {:.4f} USD ea completed with status: {}.\n'.format(float(USD_to_XTZ_order["executedQty"]),float(USD_to_XTZ_order["price"]),USD_to_XTZ_order["status"]))
		#	time.sleep(0.1)
		#	XTZ_to_BTC_order = place_order('XTZBTC','SELL','MARKET','GTC',"{:.2f}".format(float(USD_to_XTZ_order["executedQty"])),"{:.4f}".format(float(XTZ2BTC_avg_price["price"])))
		#	print('Order for {:.2f} BTC at {:.4f} XTZ ea completed with status: {}.\n'.format(float(XTZ_to_BTC_order["executedQty"]),(1/float(XTZ_to_BTC_order["price"])),XTZ_to_BTC_order["status"]))
		#	time.sleep(0.1)
		#	BTC_to_USD_order = place_order('BTCBUSD','SELL','MARKET','GTC',"{:.2f}".format(float(XTZ_to_BTC_order["executedQty"])),"{:.4f}".format(float(BTC2USD_avg_price["price"])))
		#	print('Order for {:.2f} USD at {:.4f} BTC ea completed with status: {}.\n'.format(float(BTC_to_USD_order["executedQty"]),(1/float(BTC_to_USD_order["price"])),BTC_to_USD_order["status"]))
		#	break
		elif (float(BTC2USD_avg_price["price"])*(1+fee)*factor_of_safety < (1/float(XTZ2BTC_avg_price["price"]))*(1-fee)*float(XTZ2USD_avg_price["price"])*(1-fee)):
			print("XTZ/BTC - EQ2 TRUE")
			verif_str2 = "[1] USD per BTC: {}\n[2] XTZ per BTC: {}\n[3] USD per XTZ: {}\n\n[1] USD + 0.001*[1] (0.1% fee) buys 1 BTC -> 1 BTC - 0.001 BTC (0.1% fee) buys 0.999*[2] XTZ -> 0.999*[2] XTZ - 0.001*0.999*[2] XTZ (0.1% fee) sells for (0.999^2)*[3] USD.\n"
			print(verif_str2.format(XTZ2USD_avg_price["price"],str(1/float(XTZ2BTC_avg_price["price"])),BTC2USD_avg_price["price"]))
			print('\a')
			"""
			usd_balance = client.get_asset_balance(asset='USD')
			USD_to_BTC_order = place_order('BTCBUSD','BUY','LIMIT','IOC',"{:.2f}".format(0.002*float(usd_balance["free"])/float(XTZ2BTC_avg_price["price"])),"{:.4f}".format(1.005*float(XTZ2USD_avg_price["price"])))
			print('Order for {:.2f} XTZ at {:.4f} USD ea completed with status: {}.\n'.format(float(USD_to_XTZ_order["executedQty"]),float(USD_to_XTZ_order["price"]),USD_to_XTZ_order["status"]))
			time.sleep(0.1)
			XTZ_to_BTC_order = place_order('XTZBTC','SELL','MARKET','GTC',"{:.2f}".format(float(USD_to_XTZ_order["executedQty"])),"{:.4f}".format(float(XTZ2BTC_avg_price["price"])))
			print('Order for {:.2f} BTC at {:.4f} XTZ ea completed with status: {}.\n'.format(float(XTZ_to_BTC_order["executedQty"]),(1/float(XTZ_to_BTC_order["price"])),XTZ_to_BTC_order["status"]))
			time.sleep(0.1)
			BTC_to_USD_order = place_order('BTCBUSD','SELL','MARKET','GTC',"{:.2f}".format(float(XTZ_to_BTC_order["executedQty"])),"{:.4f}".format(float(BTC2USD_avg_price["price"])))
			print('Order for {:.2f} USD at {:.4f} BTC ea completed with status: {}.\n'.format(float(BTC_to_USD_order["executedQty"]),(1/float(BTC_to_USD_order["price"])),BTC_to_USD_order["status"]))
			break
			"""
		else:
			print("FALSE")
		time.sleep(1)
		
main()
