from decimal import Decimal
from xrpl.clients import JsonRpcClient
from xrpl.wallet import Wallet
from pftpyclient.utilities.amm.amm_utilities import AMMUtilities
from pftpyclient.configuration.configuration import XRPL_TESTNET
from typing import Dict

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

def analyze_market(seed: str):
    """
    Analyze the market for a PFT/XRP trade.
    
    Args:
        seed: The seed for the wallet to use as taker
    """
    client = JsonRpcClient(XRPL_TESTNET.public_rpc_urls[0])
    wallet = Wallet.from_seed(seed)
    amm_utils = AMMUtilities(client)
    
    # Define what we want to trade
    want_amount = Decimal("1000")  # Want 1000 PFT
    spend_amount = Decimal("100")  # Willing to spend 100 XRP
    
    # Analyze the order book
    matched_amount, matching_offers = amm_utils.analyze_orderbook(
        wallet=wallet,
        want_currency="PFT",
        want_amount=want_amount,
        spend_currency="XRP",
        spend_amount=spend_amount,
        want_issuer=XRPL_TESTNET.issuer_address
    )
    
    if matched_amount > 0:
        print(f"\nFound {len(matching_offers)} matching offer(s) that could fill {matched_amount} PFT")
        print("\nMatching offers details:")
        for i, offer in enumerate(matching_offers, 1):
            print(f"\nOffer {i}:")
            print_offer_details(offer)
            
        if matched_amount < want_amount:
            remaining = want_amount - matched_amount
            print(f"\nRemaining {remaining} PFT would be placed as a new offer")
            
    else:
        print("\nNo immediate matches found. Checking competing offers...")
        competing_amount, competing_offers = amm_utils.check_competing_offers(
            wallet=wallet,
            want_currency="PFT",
            want_amount=want_amount,
            spend_currency="XRP",
            spend_amount=spend_amount,
            want_issuer=XRPL_TESTNET.issuer_address
        )
        
        if competing_amount > 0:
            print(f"\nFound {len(competing_offers)} competing offer(s) with total volume: {competing_amount} XRP")
            print("\nCompeting offers details:")
            for i, offer in enumerate(competing_offers, 1):
                print(f"\nCompeting Offer {i}:")
                print_offer_details(offer)
                
            print("\nMarket Analysis:")
            print("Your offer would be placed below these competing offers.")
            print(f"Consider adjusting your price to be more competitive.")
        else:
            print("\nNo competing offers found at this price point.")
            print("Your offer would be the first in the book at this price.")
    
    # Calculate and display some market statistics
    all_offers = matching_offers + competing_offers
    if all_offers:
        prices = [float(offer['quality']) for offer in all_offers]
        avg_price = sum(prices) / len(prices)
        min_price = min(prices)
        max_price = max(prices)
        
        print("\nMarket Statistics:")
        print(f"Number of relevant offers: {len(all_offers)}")
        print(f"Average price: {avg_price:.6f} XRP/PFT")
        print(f"Price range: {min_price:.6f} - {max_price:.6f} XRP/PFT")
        print(f"Your proposed price: {float(spend_amount/want_amount):.6f} XRP/PFT")
    
    return {
        'matched_amount': matched_amount,
        'matching_offers': matching_offers,
        'competing_amount': competing_amount if matched_amount == 0 else 0,
        'competing_offers': competing_offers if matched_amount == 0 else [],
        'proposed_price': float(spend_amount/want_amount)
    }

def main():
    SEED = "your_seed_here"
    result = analyze_market(SEED)
    
    # You can use the returned result for further processing if needed
    if result['matched_amount'] > 0:
        print("\nRecommendation: Consider executing the trade immediately")
    elif result['competing_amount'] > 0:
        print("\nRecommendation: Consider adjusting your price to be more competitive")
    else:
        print("\nRecommendation: Safe to place order as first in book")

if __name__ == "__main__":
    main()