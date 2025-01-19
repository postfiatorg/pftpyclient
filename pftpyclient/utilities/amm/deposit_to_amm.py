from decimal import Decimal
import time
from xrpl.clients import JsonRpcClient
from xrpl.wallet import Wallet
from pftpyclient.utilities.amm.amm_utilities import AMMUtilities
from pftpyclient.configuration.configuration import XRPL_TESTNET, XRPL_MAINNET

SEED = "your seed here"
USE_TESTNET = True

def deposit_to_amm(seed: str, use_testnet: bool = True):
    # Setup
    network = XRPL_TESTNET if use_testnet else XRPL_MAINNET
    client = JsonRpcClient(network.public_rpc_urls[0])
    wallet = Wallet.from_seed(seed)
    
    # Initialize utilities
    amm_utils = AMMUtilities(client)
    
    # Print initial balances
    print("Initial trust lines:")
    initial_lines = amm_utils.get_account_lines(wallet.classic_address)
    print_trust_lines(initial_lines)
    
    try:
        # Deposit both assets
        print("\nDepositing both PFT and XRP...")
        response = amm_utils.deposit_both_assets(
            wallet=wallet,
            pft_amount=Decimal(1000),  # 1000 PFT
            xrp_amount=Decimal(1),     # 1 XRP
            pft_issuer=network.issuer_address
        )
        print("Deposit response:", response)
        
        # Wait for validation
        time.sleep(5)
        
        # Check updated balances
        print("\nUpdated trust lines:")
        updated_lines = amm_utils.get_account_lines(wallet.classic_address)
        print_trust_lines(updated_lines)
        
    except Exception as e:
        print(f"Error during deposit: {e}")

def print_trust_lines(lines_result: dict):
    """Helper function to print trust line information"""
    for line in lines_result.get("lines", []):
        print(f"Currency: {line['currency']}")
        print(f"Balance: {line['balance']}")
        print(f"Issuer: {line['account']}")
        print("---")

def main():
    deposit_to_amm(SEED, USE_TESTNET)

if __name__ == "__main__":
    main()