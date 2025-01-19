from decimal import Decimal
from xrpl.clients import JsonRpcClient
from xrpl.wallet import Wallet
from pftpyclient.utilities.amm.amm_utilities import AMMUtilities
from pftpyclient.configuration.configuration import XRPL_TESTNET

def execute_trade(seed: str, is_buy: bool = True):
    """
    Execute a trade on the XRPL.
    
    Args:
        seed: The wallet seed
        is_buy: True to buy PFT with XRP, False to sell PFT for XRP
    """
    # Setup
    client = JsonRpcClient(XRPL_TESTNET.public_rpc_urls[0])
    wallet = Wallet.from_seed(seed)
    amm_utils = AMMUtilities(client)
    
    # Get initial balances
    print("\nChecking initial balances...")
    initial_lines = amm_utils.get_account_lines(wallet.classic_address)
    
    # Define trade parameters
    pft_amount = Decimal("1000")  # Amount of PFT
    xrp_amount = Decimal("100")   # Amount of XRP
    
    try:
        if is_buy:
            response = amm_utils.buy_pft_with_xrp(
                wallet=wallet,
                pft_amount=pft_amount,
                xrp_amount=xrp_amount,
                pft_issuer=XRPL_TESTNET.issuer_address
            )
        else:
            response = amm_utils.sell_pft_for_xrp(
                wallet=wallet,
                pft_amount=pft_amount,
                xrp_amount=xrp_amount,
                pft_issuer=XRPL_TESTNET.issuer_address
            )
        
        # Wait a moment for the transaction to be processed
        import time
        time.sleep(5)
        
        # Check final balances
        print("\nChecking final balances...")
        final_lines = amm_utils.get_account_lines(wallet.classic_address)
        
    except Exception as e:
        print(f"Error executing trade: {e}")

def main():
    SEED = "your_seed_here"
    
    # Buy PFT with XRP
    print("\nExecuting buy order...")
    execute_trade(SEED, is_buy=True)
    
    # Or sell PFT for XRP
    # print("\nExecuting sell order...")
    # execute_trade(SEED, is_buy=False)

if __name__ == "__main__":
    main()