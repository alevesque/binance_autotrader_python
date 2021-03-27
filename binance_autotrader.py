from binance.client import Client
from binance.exceptions import *
#from binance.enums import *
import time
import sys
import requests

api_key = "3dUerAA0KFYj9BPhLajkRdx3yxcxgSWqyayemilGsFft4lmyXc9iqyATO5D5xVDX"
api_secret = "8CgimgMn9JFajxCxfO49bAz1Sx8yGSxUuoCyg3Mix1i7SQlzXzuvBiWnVFBcBXAC"

try:
	#tld='' is used to denote which server to use (.com or .us)
	client = Client(api_key, api_secret,{"timeout": 6},tld='us')
	print("Client Initialized Successfully")
except requests.exceptions.RequestException as e:
	print("Connection error - Initializing API client")
	sys.exit()

def main():

	"""fee can be reduced by extensive BNB holdings, high monthly trade volume (USD), or 25% discount for paying fees with BNB. see https://www.binance.us/en/fee/schedule"""
	fee = 0.001

	"""factor of safety for trades ensures only trades with x% profit get executed, to help prevent loss"""
	factor_of_safety=1.005 #1.0075
	spend_limit_for_testing = 0.01
	buy_price_increase = 1.0005 # place initial order slighly above calculated price to help ensure order fills
	trading_pairs = [("ADAUSD","ADABTC","ADA"),("BCHUSD","BCHBTC","BCH"),("BNBUSD","BNBBTC","BNB"),("ETHUSD","ETHBTC","ETH"),("LINKUSD","LINKBTC","LINK"),("LTCUSD","LTCBTC","LTC"),("UNIUSD","UNIBTC","UNI"),("VETUSD","VETBTC","VET"),("XTZUSD","XTZBTC","XTZ")]
	
	i=0

	while 1:
		i+=1
		for x in trading_pairs:

			try:
				USD_per_alt = client.get_avg_price(symbol=x[0])
				BTC_per_alt = client.get_avg_price(symbol=x[1])
				USD_per_BTC = client.get_avg_price(symbol='BTCUSD')
			except BinanceAPIException as e:
				print(e)
				print("Querying prices - something went wrong, likely connection issues. Trying again in 5s.")
				time.sleep(5)
				break
			except requests.exceptions.RequestException as e:
				print(e)
				print("Error - Check connection - Querying prices")
				time.sleep(5)
				break

			if i % 5 == 0:
				print("Price: {} USD per {}".format(USD_per_alt["price"],x[2]))
				print("Price: {} BTC per {}".format(BTC_per_alt["price"],x[2]))
				print("Price: {} USD per BTC\n".format(USD_per_BTC["price"]))
				if x == trading_pairs[-1]:
					print("-----------------")
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
			
			if (float(USD_per_alt["price"])*(1+fee)*factor_of_safety < float(BTC_per_alt["price"])*(1-fee)*float(USD_per_BTC["price"])*(1-fee)):
				
				print("\a {} - EQ1 TRUE\n".format(x[2]))
				verif_str1 = "[1] USD per {}: {}\n[2] BTC per {}: {}\n[3] USD per BTC: {}\n\n[1] USD + 0.001*[1] (0.1% fee) buys 1 {} -> 1 {} - 0.001 {} (0.1% fee) buys 0.999*[2] BTC -> 0.999*[2] BTC - 0.001*0.999*[2] BTC (0.1% fee) sells for (0.999^2)*[3] USD.\n"
				print(verif_str1.format(x[2],USD_per_alt["price"],x[2],BTC_per_alt["price"],USD_per_BTC["price"],x[2],x[2],x[2]))
				
				usd_balance = check_balance('USD')
				if (usd_balance == -1):
					break
							
				USD_per_alt_order = place_order(symbol=x[0],side='BUY',type='LIMIT',timeInForce='IOC',quantity="{0:.{1}f}".format(spend_limit_for_testing*float(usd_balance)/float(USD_per_alt["price"]),alt_precision(x[2])),price="{:.4f}".format(buy_price_increase*float(USD_per_alt["price"])))
				
				if (USD_per_alt_order == -1):
					break
				else:
					print('Order for {:.2f} {} at {:.4f} USD ea completed with status: {}.\n'.format(float(USD_per_alt_order["executedQty"]),x[2],float(USD_per_alt_order["price"]),USD_per_alt_order["status"]))
				
				if(USD_per_alt_order["status"]=='FILLED'):
					BTC_per_alt_order = place_order(symbol=x[1],side='SELL',type='MARKET',quantity="{0:.{1}f}".format(float(USD_per_alt_order["executedQty"]),alt_precision(x[2])))#,"{:.4f}".format(float(BTC_per_alt["price"])))
									
					if (BTC_per_alt_order == -1):
						break
					else:
						print(BTC_per_alt_order)
						print('Order for {:.2f} BTC at {} {} ea completed with status: {}.\n'.format(float(BTC_per_alt_order["executedQty"]),(1/float(BTC_per_alt_order["fills"][0]["price"])),x[2],BTC_per_alt_order["status"]))

				
					USD_per_BTC_order = place_order(symbol='BTCUSD',side='SELL',type='MARKET',quantity="{:.6f}".format(float(BTC_per_alt_order["executedQty"])))#,"{:.4f}".format(float(USD_per_BTC["price"])))
					
					if (USD_per_BTC_order == -1):
						break
					else:
						print('Order for {:.2f} USD at {:.6f} BTC ea completed with status: {}.\n'.format(float(USD_per_BTC_order["executedQty"]),(1/float(USD_per_BTC_order["fills"][0]["price"])),USD_per_BTC_order["status"]))
		
					usd_balance = check_balance('USD')
				
					time.sleep(10000)
			elif (float(USD_per_BTC["price"])*(1+fee)*factor_of_safety < float(USD_per_alt["price"])*(1-fee)/(float(BTC_per_alt["price"])*(1-fee))):
				
				print("\a {} - EQ2 TRUE\n".format(x[2]))
				verif_str2 = "[1] USD per BTC: {}\n[2] {} per BTC: {}\n[3] USD per {}: {}\n\n[1] USD + 0.001*[1] (0.1% fee) buys 1 BTC -> 1 BTC - 0.001 BTC (0.1% fee) buys 0.999*[2] {} -> 0.999*[2] {} - 0.001*0.999*[2] {} (0.1% fee) sells for (0.999^2)*[3] USD.\n"
				print(verif_str2.format(USD_per_BTC["price"],x[2],str(1/float(BTC_per_alt["price"])),x[2],USD_per_alt["price"],x[2],x[2],x[2]))
				
				usd_balance = check_balance('USD')
				if (usd_balance == -1):
					break

				btc_quantity = round((spend_limit_for_testing*float(usd_balance)/float(USD_per_BTC["price"]))/BTC_per_alt["price"],alt_precision(x[2]))*BTC_per_alt["price"]
				print(btc_quantity)

				USD_per_BTC_order = place_order(symbol='BTCUSD',side='BUY',type='LIMIT',timeInForce='IOC',quantity="{:.6f}".format(btc_quantity),price="{:.2f}".format(buy_price_increase*float(USD_per_BTC["price"])))
				if (USD_per_BTC_order == -1):
					break
				else:
					print('Order for {} BTC at {} USD ea completed with status: {}.\n'.format(float(USD_per_BTC_order["executedQty"]),float(USD_per_BTC_order["price"]),USD_per_BTC_order["status"]))
				
				if(USD_per_BTC_order["status"]=='FILLED'):

					BTC_per_alt_order = place_order(symbol=x[1],side='BUY',type='MARKET',quantity="{0:.{1}f}".format(float(USD_per_BTC_order["executedQty"])/float(BTC_per_alt["price"]),alt_precision(x[2])))#,"{:.4f}".format(float(BTC_per_alt["price"])))
					
					if (BTC_per_alt_order == -1):
						break
					else:
						print('Order for {} {} at {:.6f} BTC ea completed with status: {}.\n'.format(float(BTC_per_alt_order["executedQty"]),x[2],float(BTC_per_alt_order["fills"][0]["price"]),BTC_per_alt_order["status"]))
					
					USD_per_alt_order = place_order(symbol=x[0],side='SELL',type='MARKET',quantity="{0:.{1}f}".format(float(BTC_per_alt_order["executedQty"]),alt_precision(x[2])))#,"{:.4f}".format(float(USD_per_alt["price"])))
					
					if (USD_per_alt_order == -1):
						break
					else:
						print('Order for {:.2f} USD at {:.4f} {} ea completed with status: {}.\n'.format(float(USD_per_BTC_order["executedQty"]),(1/float(USD_per_BTC_order["fills"][0]["price"])),x[2],USD_per_BTC_order["status"]))
				
					usd_balance = check_balance('USD')	
					time.sleep(10000)			
			
		time.sleep(1)

def place_order(**order_parameters): #symbol, side, type, timeInForce,quantity,price):
	print(order_parameters)

	try:
		order = client.create_order(**order_parameters)
		return order
	except BinanceAPIException as e:
		print(e)
		print("placing order - something went wrong.")
		time.sleep(1)
		return -1
	except requests.exceptions.RequestException as e:
		print(e)
		print("Error - Check connection - placing order")
		time.sleep(1)
		return -1

def check_balance(requested_asset):
	try:
		asset_balance = client.get_asset_balance(asset=requested_asset)
		print("Balance: {} {}".format(asset_balance["free"],requested_asset))
		return asset_balance["free"]
	except BinanceAPIException as e:
		print(e)
		print("Querying {} balance - something went wrong.".format(requested_asset))
		time.sleep(1)
		return -1
	except requests.exceptions.RequestException as e:
		print(e)
		print("Error - Check connection - Querying {} balance.".format(requested_asset))
		time.sleep(1)
		return -1

def alt_precision(x):
	return {
		'ADA': 0,
		'BCH': 2,
		'BNB': 2,
		'ETH': 2,
		'LINK': 1,
		'LTC': 2,
		'UNI': 0,
		'VET': 0,
		'XTZ': 2,
	}

main()
