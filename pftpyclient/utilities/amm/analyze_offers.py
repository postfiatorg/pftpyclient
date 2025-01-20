from decimal import Decimal
import asyncio
from pftpyclient.utilities.amm.amm_utilities import AMMUtilities
from pftpyclient.configuration.configuration import ConfigurationManager, get_network_config, Network
from typing import Dict, Any

def print_offer_details(offer: Dict) -> None:
    """Helper function to print details of an individual offer"""
    print("\nOffer Details:")
    print(f"Account: {offer.get('Account', 'Unknown')}")
    print(f"Quality: {float(offer.get('quality', 0)):.8f}")
    
    # Handle TakerGets (what you would receive)
    taker_gets = offer.get('TakerGets', {})
    if isinstance(taker_gets, dict):
        print(f"TakerGets: {taker_gets.get('value')} {taker_gets.get('currency')}")
    else:
        print(f"TakerGets: {float(taker_gets) / 1_000_000} XRP")  # Convert drops to XRP
    
    # Handle TakerPays (what you would pay)
    taker_pays = offer.get('TakerPays', {})
    if isinstance(taker_pays, dict):
        print(f"TakerPays: {taker_pays.get('value')} {taker_pays.get('currency')}")
    else:
        print(f"TakerPays: {float(taker_pays) / 1_000_000} XRP")  # Convert drops to XRP
    
    if 'owner_funds' in offer:
        print(f"Owner Funds: {offer['owner_funds']}")

async def analyze_market():
    """Analyze the PFT/XRP market on XRPL."""
    config = ConfigurationManager()
    network_config = get_network_config(Network.XRPL_TESTNET)
    amm_utils = AMMUtilities(config)
    
    # Get bids (offers buying PFT for XRP)
    pft_bids = await amm_utils.get_orderbook(
        taker_gets_currency="XRP",
        taker_pays_currency="PFT",
        taker_pays_issuer=network_config.issuer_address
    )

    # Get asks (offers selling PFT for XRP)
    pft_asks = await amm_utils.get_orderbook(
        taker_gets_currency="PFT",
        taker_pays_currency="XRP",
        taker_gets_issuer=network_config.issuer_address
    )

    print("\n=== PFT/XRP Orderbook ===")
    
    print("\nAsks (Selling PFT):")
    print("Price (PFT/XRP) | Amount PFT    | Amount XRP")
    print("-" * 45)
    for offer in pft_asks.get('offers', []):
        price = 1.0 / amm_utils.convert_quality_to_price(offer, side="asks")
        pft_quantity = float(offer['TakerGets']['value'] if isinstance(offer['TakerGets'], dict) else 0)
        xrp_quantity = float(offer['TakerPays']) / 1_000_000  # Convert drops to XRP
        print(f"{price:13.9f} | {pft_quantity:11.2f} | {xrp_quantity:10.2f}")

    print("\nBids (Buying PFT):")
    print("Price (PFT/XRP) | Amount PFT    | Amount XRP")
    print("-" * 45)
    for offer in pft_bids.get('offers', []):
        price = 1.0 / amm_utils.convert_quality_to_price(offer, side="bids")
        pft_quantity = float(offer['TakerPays']['value'] if isinstance(offer['TakerPays'], dict) else 0)
        xrp_quantity = float(offer['TakerGets']) / 1_000_000  # Convert drops to XRP
        print(f"{price:13.9f} | {pft_quantity:11.2f} | {xrp_quantity:10.2f}")
    all_asks = pft_asks.get('offers', [])
    all_bids = pft_bids.get('offers', [])
    
    if all_asks:
        best_ask = 1.0 / amm_utils.convert_quality_to_price(all_asks[0], side="asks")
        print(f"Best ask (lowest sell): {best_ask:.6f} PFT/XRP")
    else:
        print("No asks (sell orders) in the book")
        
    if all_bids:
        best_bid = 1.0 / amm_utils.convert_quality_to_price(all_bids[0], side="bids")
        print(f"Best bid (highest buy): {best_bid:.6f} PFT/XRP")
    else:
        print("No bids (buy orders) in the book")

    # Market depth analysis
    total_ask_volume_pft = sum(
        float(offer['TakerGets']['value']) 
        for offer in all_asks 
        if isinstance(offer['TakerGets'], dict)
    )
    total_bid_volume_pft = sum(
        float(offer['TakerPays']['value']) 
        for offer in all_bids 
        if isinstance(offer['TakerPays'], dict)
    )
    
    print(f"\nMarket Depth:")
    print(f"Total PFT for sale: {total_ask_volume_pft:,.2f} PFT")
    print(f"Total PFT bid for: {total_bid_volume_pft:,.2f} PFT")

    return {
        'asks': pft_asks.get('offers', []),
        'bids': pft_bids.get('offers', []),
        # 'your_price': your_price,
        'best_ask': best_ask if all_asks else None,
        'best_bid': best_bid if all_bids else None
    }

async def main():
    await analyze_market()

if __name__ == "__main__":
    asyncio.run(main())