from binance.client import Client
#from binance.enums import *
import time

api_key = "3dUerAA0KFYj9BPhLajkRdx3yxcxgSWqyayemilGsFft4lmyXc9iqyATO5D5xVDX"
api_secret = "8CgimgMn9JFajxCxfO49bAz1Sx8yGSxUuoCyg3Mix1i7SQlzXzuvBiWnVFBcBXAC"

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

	"""fee can be reduced by extensive BNB holdings, high monthly trade volume (USD), or 25% discount for paying fees with BNB. see https://www.binance.us/en/fee/schedule"""
	fee = 0.001

	"""factor of safety for trades ensures only trades with x% profit get executed, to help prevent loss"""
	factor_of_safety=1.0075
	spend_limit_for_testing = 0.02

	trading_pairs = [("ADAUSD","ADABTC","ADA"),("BCHUSD","BCHBTC","BCH"),("BNBUSD","BNBBTC","BNB"),("ETHUSD","ETHBTC","ETH"),("LINKUSD","LINKBTC","LINK"),("LTCUSD","LTCBTC","LTC"),("UNIUSD","UNIBTC","UNI"),("VETUSD","VETBTC","VET"),("XTZUSD","XTZBTC","XTZ")]
	i=0
	while 1:
		i+=1
		for x in trading_pairs:
			USD_per_alt = client.get_avg_price(symbol=x[0])
			BTC_per_alt = client.get_avg_price(symbol=x[1])
			USD_per_BTC = client.get_avg_price(symbol='BTCBUSD')

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
				
				usd_balance = client.get_asset_balance(asset='USD')
				print("USD balance before trade: {}".format(usd_balance["free"]))

				if (x[2]=="VET"):
					USD_per_alt_order = place_order(x[0],'BUY','LIMIT','IOC',"{:.0f}".format(spend_limit_for_testing*float(usd_balance["free"])/float(USD_per_alt["price"])),"{:.4f}".format(1.001*float(USD_per_alt["price"])))
				elif (x[2]=="ADA"):
					USD_per_alt_order = place_order(x[0],'BUY','LIMIT','IOC',"{:.1f}".format(spend_limit_for_testing*float(usd_balance["free"])/float(USD_per_alt["price"])),"{:.4f}".format(1.001*float(USD_per_alt["price"])))
				else:
					USD_per_alt_order = place_order(x[0],'BUY','LIMIT','IOC',"{:.4f}".format(spend_limit_for_testing*float(usd_balance["free"])/float(USD_per_alt["price"])),"{:.4f}".format(1.001*float(USD_per_alt["price"])))
				
				print('Order for {:.2f} {} at {:.4f} USD ea completed with status: {}.\n'.format(float(USD_per_alt_order["executedQty"]),x[2],float(USD_per_alt_order["price"]),USD_per_alt_order["status"]))
				
				#time.sleep(0.1)
				
				BTC_per_alt_order = place_order(x[1],'SELL','MARKET',"{:.4f}".format(float(USD_per_alt_order["executedQty"])))#,"{:.4f}".format(float(BTC_per_alt["price"])))
				print('Order for {:.2f} BTC at {:.4f} {} ea completed with status: {}.\n'.format(float(BTC_per_alt_order["executedQty"]),x[2],(1/float(BTC_per_alt_order["price"])),BTC_per_alt_order["status"]))
				
				#time.sleep(0.1)
				
				USD_per_BTC_order = place_order('BTCBUSD','SELL','MARKET',"{:.4f}".format(float(BTC_per_alt_order["executedQty"])))#,"{:.4f}".format(float(USD_per_BTC["price"])))
				print('Order for {:.2f} USD at {:.4f} BTC ea completed with status: {}.\n'.format(float(USD_per_BTC_order["executedQty"]),(1/float(USD_per_BTC_order["price"])),USD_per_BTC_order["status"]))
				
				usd_balance = client.get_asset_balance(asset='USD')
				print("USD balance after trade: {}".format(usd_balance["free"]))
				break

			elif (float(USD_per_BTC["price"])*(1+fee)*factor_of_safety < float(USD_per_alt["price"])*(1-fee)/(float(BTC_per_alt["price"])*(1-fee))):
				print("\a {} - EQ2 TRUE\n".format(x[2]))
				verif_str2 = "[1] USD per BTC: {}\n[2] {} per BTC: {}\n[3] USD per {}: {}\n\n[1] USD + 0.001*[1] (0.1% fee) buys 1 BTC -> 1 BTC - 0.001 BTC (0.1% fee) buys 0.999*[2] {} -> 0.999*[2] {} - 0.001*0.999*[2] {} (0.1% fee) sells for (0.999^2)*[3] USD.\n"
				print(verif_str2.format(USD_per_BTC["price"],x[2],str(1/float(BTC_per_alt["price"])),x[2],USD_per_alt["price"],x[2],x[2],x[2]))
				
				usd_balance = client.get_asset_balance(asset='USD')
				print("USD balance before trade: {}".format(usd_balance["free"]))

				USD_per_BTC_order = place_order('BTCBUSD','BUY','LIMIT','IOC',"{:.4f}".format(spend_limit_for_testing*float(usd_balance["free"])/float(USD_per_BTC["price"])),"{:.2f}".format(1.001*float(USD_per_BTC["price"])))
				print('Order for {:.2f} BTC at {:.4f} USD ea completed with status: {}.\n'.format(float(USD_per_BTC_order["executedQty"]),float(USD_per_BTC_order["price"]),USD_per_BTC_order["status"]))
				#
				time.sleep(0.1)

				if (x[2]=="VET"):
					BTC_per_alt_order = place_order(x[1],'SELL','MARKET',"{:.0f}".format(float(USD_per_BTC_order["executedQty"])))#,"{:.4f}".format(float(BTC_per_alt["price"])))
				elif (x[2]=="ADA"):
					BTC_per_alt_order = place_order(x[1],'SELL','MARKET',"{:.1f}".format(float(USD_per_BTC_order["executedQty"])))#,"{:.4f}".format(float(BTC_per_alt["price"])))
				else:
					BTC_per_alt_order = place_order(x[1],'SELL','MARKET',"{:.4f}".format(float(USD_per_BTC_order["executedQty"])))#,"{:.4f}".format(float(BTC_per_alt["price"])))
				
				print('Order for {:.2f} {} at {:.4f} {} ea completed with status: {}.\n'.format(float(BTC_per_alt_order["executedQty"]),x[2],(1/float(BTC_per_alt_order["price"])),x[2],BTC_per_alt_order["status"]))
				
				#time.sleep(0.1)
				
				USD_per_alt_order = place_order(x[0],'SELL','MARKET',"{:.4f}".format(float(BTC_per_alt_order["executedQty"])))#,"{:.4f}".format(float(USD_per_alt["price"])))
				print('Order for {:.2f} USD at {:.4f} {} ea completed with status: {}.\n'.format(float(USD_per_BTC_order["executedQty"]),(1/float(USD_per_BTC_order["price"])),x[2],USD_per_BTC_order["status"]))
			
				usd_balance = client.get_asset_balance(asset='USD')
				print("USD balance after trade: {}".format(usd_balance["free"]))
				break
				
			
		time.sleep(1)
		
main()
