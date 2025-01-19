from decimal import Decimal
import time
from xrpl.clients import JsonRpcClient
from xrpl.wallet import Wallet
from pftpyclient.utilities.amm.amm_utilities import AMMUtilities
from pftpyclient.configuration.configuration import XRPL_TESTNET, XRPL_MAINNET

SEED = "your seed here"
USE_TESTNET = True

def print_trust_lines(lines_result: dict):
    """Helper function to print trust line information"""
    print("\nTrust Lines:")
    for line in lines_result.get("lines", []):
        print(f"Currency: {line['currency']}")
        print(f"Balance: {line['balance']}")
        print(f"Issuer: {line['account']}")
        print("---")

def create_amm(seed, use_testnet=True):
    if use_testnet:
        network = XRPL_TESTNET
    else:
        network = XRPL_MAINNET

    # Connect to testnet
    client = JsonRpcClient(network.public_rpc_urls[0])

    # Your wallet with sufficient XRP and PFT tokens
    wallet = Wallet.from_seed(seed)

    # Initialize the utilities
    amm_utils = AMMUtilities(client)

    # Check if AMM already exists
    try:
        amm_info = amm_utils.get_pft_xrp_amm_info(network.issuer_address)
        print("AMM already exists:")
        print(f"AMM Account: {amm_info['amm']['account']}")
        print(f"Trading Fee: {amm_info['amm']['trading_fee']/1000}%")
        print(f"XRP Balance: {int(amm_info['amm']['amount'])/1000000} XRP")
        print(f"PFT Balance: {amm_info['amm']['amount2']['value']} PFT")

        # Check trust lines for existing AMM
        print("\nChecking trust lines for LP tokens...")
        lp_lines = amm_utils.get_account_lines(
            wallet.classic_address,
            # peer=amm_info['amm']['account']
        )
        print_trust_lines(lp_lines)
        return None
        
    except Exception as e:
        error_resp = getattr(e, 'data', {}).get('error')
        if error_resp == 'actNotFound':
            print("No AMM exists yet for the PFT/XRP pair. Creating new AMM...")
            
            # Create a PFT/XRP AMM
            response = amm_utils.prepare_pft_xrp_amm_create(
                wallet=wallet,
                pft_amount=Decimal(100000),  # Amount of PFT tokens
                xrp_amount=Decimal(10),      # Amount of XRP
                pft_issuer=network.issuer_address,
                trading_fee=500              # 0.5% fee
            )
            
            print("AMM Creation Response:")
            print(response)

            print("\nVerifying AMM creation...")
            # Wait for transaction to be validated (typically 4-5 seconds on testnet)
            time.sleep(5)

            try:
                # Wait a moment and check the AMM info
                amm_info = amm_utils.get_pft_xrp_amm_info(network.issuer_address)
                print("\nNew AMM Details:")
                print(f"AMM Account: {amm_info['amm']['account']}")
                print(f"Trading Fee: {amm_info['amm']['trading_fee']/1000}%")
                print(f"XRP Balance: {int(amm_info['amm']['amount'])/1000000} XRP")
                print(f"PFT Balance: {amm_info['amm']['amount2']['value']} PFT")

                # Check updated trust lines after AMM creation
                print("\nChecking updated trust lines...")
                updated_lines = amm_utils.get_account_lines(
                    wallet.classic_address,
                    # peer=amm_info['amm']['account']
                )
                print_trust_lines(updated_lines)
            except Exception as verify_error:
                print(f"Error verifying AMM creation: {verify_error}")
            
            return response
        else:
            print(f"Unexpected error: {e}")
            raise e

    print(response)


def main():
    create_amm(SEED, USE_TESTNET)


if __name__ == "__main__":
    main()

# First AMM create response object
# Response(status=<ResponseStatus.SUCCESS: 'success'>, result={'tx_json': {'Account': 'rNC2hS269hTvMZwNakwHPkw4VeZNwzpS2E', 'Amount': {'currency': 'PFT', 'issuer': 'rLX2tgumpiUE6kjr757Ao8HWiJzC8uuBSN', 'value': '100000'}, 'Amount2': '10000000', 'Fee': '2000000', 'Flags': 0, 'LastLedgerSequence': 4092584, 'Sequence': 2458692, 'SigningPubKey': 'ED76ED98D763628AB637699592D447FB0C58739DA225B26F6B73B918B4795496CA', 'TradingFee': 500, 'TransactionType': 'AMMCreate', 'TxnSignature': '65BF5B37D4E0B74F02F0EAFBA3D01D082B08EF98555C7D14C7A8E7046F89EF871B588325EDDF897842B3DEB3CBBF87D8A3E9A17C29DFD68FECCC17CED66FAB0D', 'date': 790577362, 'ledger_index': 4092566}, 'ctid': 'C03E729600000001', 'hash': 'D5601E897F5C8947A429B28744926EFA2CB2981B4E53288356ABAF50C6AA55E4', 'meta': {'AffectedNodes': [{'ModifiedNode': {'FinalFields': {'Flags': 0, 'Owner': 'rNC2hS269hTvMZwNakwHPkw4VeZNwzpS2E', 'RootIndex': '106E9A146E83B4713658DDF8B52F627D1C319123E423809B4592E84C706FDF05'}, 'LedgerEntryType': 'DirectoryNode', 'LedgerIndex': '106E9A146E83B4713658DDF8B52F627D1C319123E423809B4592E84C706FDF05', 'PreviousTxnID': '0C69C8B5D9D5F47EF4015332DF39B7590050EC67E31875D3AC70B5B1B0A5A9F2', 'PreviousTxnLgrSeq': 2458724}}, {'CreatedNode': {'LedgerEntryType': 'RippleState', 'LedgerIndex': '3DF4505DB34A0C55164058E1BFC269359677A69AF75529A46D56A01FE3586942', 'NewFields': {'Balance': {'currency': '03C54B0BDA6CC647878F552C543B088C333AE206', 'issuer': 'rrrrrrrrrrrrrrrrrrrrBZbvji', 'value': '1000000'}, 'Flags': 1114112, 'HighLimit': {'currency': '03C54B0BDA6CC647878F552C543B088C333AE206', 'issuer': 'rLrJKfsY3TBbXu7iYCBm1fnKZNB1Q7UEUm', 'value': '0'}, 'LowLimit': {'currency': '03C54B0BDA6CC647878F552C543B088C333AE206', 'issuer': 'rNC2hS269hTvMZwNakwHPkw4VeZNwzpS2E', 'value': '0'}}}}, {'CreatedNode': {'LedgerEntryType': 'RippleState', 'LedgerIndex': '5582B6A661A04E7D9D4B3DF82FFF897762EEECDB6EC33469402637849C7CE621', 'NewFields': {'Balance': {'currency': 'PFT', 'issuer': 'rrrrrrrrrrrrrrrrrrrrBZbvji', 'value': '100000'}, 'Flags': 16842752, 'HighLimit': {'currency': 'PFT', 'issuer': 'rLX2tgumpiUE6kjr757Ao8HWiJzC8uuBSN', 'value': '0'}, 'HighNode': '1', 'LowLimit': {'currency': 'PFT', 'issuer': 'rLrJKfsY3TBbXu7iYCBm1fnKZNB1Q7UEUm', 'value': '0'}}}}, {'ModifiedNode': {'FinalFields': {'Account': 'rNC2hS269hTvMZwNakwHPkw4VeZNwzpS2E', 'Balance': '106994219', 'Flags': 262144, 'OwnerCount': 2, 'Sequence': 2458693}, 'LedgerEntryType': 'AccountRoot', 'LedgerIndex': '79008BC8C1198105ED0BD6F80BA3E501B09769FCBF46E962E8335F6B7631005F', 'PreviousFields': {'Balance': '118994219', 'OwnerCount': 1, 'Sequence': 2458692}, 'PreviousTxnID': '6C3CA8ED90E3F5FE0C26433E3458CC3041219AD5DB9B90B775FC89C51CCB492D', 'PreviousTxnLgrSeq': 4055862}}, {'CreatedNode': {'LedgerEntryType': 'AMM', 'LedgerIndex': '81AD0EE0A1C8535B373DAE31119F30E6618703955A5C170F0E8105DA8033B41D', 'NewFields': {'Account': 'rLrJKfsY3TBbXu7iYCBm1fnKZNB1Q7UEUm', 'Asset2': {'currency': 'PFT', 'issuer': 'rLX2tgumpiUE6kjr757Ao8HWiJzC8uuBSN'}, 'AuctionSlot': {'Account': 'rNC2hS269hTvMZwNakwHPkw4VeZNwzpS2E', 'DiscountedFee': 50, 'Expiration': 790663761, 'Price': {'currency': '03C54B0BDA6CC647878F552C543B088C333AE206', 'issuer': 'rLrJKfsY3TBbXu7iYCBm1fnKZNB1Q7UEUm', 'value': '0'}}, 'LPTokenBalance': {'currency': '03C54B0BDA6CC647878F552C543B088C333AE206', 'issuer': 'rLrJKfsY3TBbXu7iYCBm1fnKZNB1Q7UEUm', 'value': '1000000'}, 'TradingFee': 500, 'VoteSlots': [{'VoteEntry': {'Account': 'rNC2hS269hTvMZwNakwHPkw4VeZNwzpS2E', 'TradingFee': 500, 'VoteWeight': 100000}}]}}}, {'ModifiedNode': {'FinalFields': {'Flags': 0, 'Owner': 'rLX2tgumpiUE6kjr757Ao8HWiJzC8uuBSN', 'RootIndex': 'FDEA1276F04D01B9F3DB5B662D1CAD44B6ED08F6420983494B5ADFBAC75666B6'}, 'LedgerEntryType': 'DirectoryNode', 'LedgerIndex': '832581DE6CD9FDA0AB5903FAC38294F76650AFC1EBA4A3AF98CA22C992029A56', 'PreviousTxnID': 'C7378BCCB6EE2DEE13E31D7C7D751C7C7F47450AEAC139A3279AA72A6CB11029', 'PreviousTxnLgrSeq': 4054811}}, {'CreatedNode': {'LedgerEntryType': 'AccountRoot', 'LedgerIndex': '9005F2AF95249CC0F033B45725B6829FE440FB2448BD6AA6307B37B89544A1AF', 'NewFields': {'AMMID': '81AD0EE0A1C8535B373DAE31119F30E6618703955A5C170F0E8105DA8033B41D', 'Account': 'rLrJKfsY3TBbXu7iYCBm1fnKZNB1Q7UEUm', 'Balance': '10000000', 'Flags': 26214400, 'OwnerCount': 1, 'Sequence': 4092566}}}, {'ModifiedNode': {'LedgerEntryType': 'AccountRoot', 'LedgerIndex': 'A7256E4BAE9C50D191DC109EE0E03666E336FC11B9D13295BB8A326884BC1835', 'PreviousTxnID': 'C7378BCCB6EE2DEE13E31D7C7D751C7C7F47450AEAC139A3279AA72A6CB11029', 'PreviousTxnLgrSeq': 4054811}}, {'ModifiedNode': {'FinalFields': {'Balance': {'currency': 'PFT', 'issuer': 'rrrrrrrrrrrrrrrrrrrrBZbvji', 'value': '98802888903.999'}, 'Flags': 65536, 'HighLimit': {'currency': 'PFT', 'issuer': 'rLX2tgumpiUE6kjr757Ao8HWiJzC8uuBSN', 'value': '0'}, 'HighNode': '0', 'LowLimit': {'currency': 'PFT', 'issuer': 'rNC2hS269hTvMZwNakwHPkw4VeZNwzpS2E', 'value': '1000000000000000e-4'}, 'LowNode': '0'}, 'LedgerEntryType': 'RippleState', 'LedgerIndex': 'B57F710402E5FB9719848EFAD01174008D85B3B3AE42C23E83B5E63DA7BF36D5', 'PreviousFields': {'Balance': {'currency': 'PFT', 'issuer': 'rrrrrrrrrrrrrrrrrrrrBZbvji', 'value': '98802988903.999'}}, 'PreviousTxnID': '5006A5E4825C068CE3FBFC1BB818D0E2E9CEBC6E58918DFA13D0DEF5E9C5BC80', 'PreviousTxnLgrSeq': 4054350}}, {'CreatedNode': {'LedgerEntryType': 'DirectoryNode', 'LedgerIndex': 'DA2712D526DAFB278FC3495246D88017FF75052CE764CB1918CAE13A5E189F05', 'NewFields': {'Owner': 'rLrJKfsY3TBbXu7iYCBm1fnKZNB1Q7UEUm', 'RootIndex': 'DA2712D526DAFB278FC3495246D88017FF75052CE764CB1918CAE13A5E189F05'}}}], 'TransactionIndex': 0, 'TransactionResult': 'tesSUCCESS'}, 'validated': True, 'ledger_index': 4092566, 'ledger_hash': '19DB7FDE0D3B21094BA884B63DE56EDD82CD5BF11628B8B992282237FD0270EA', 'close_time_iso': '2025-01-19T04:49:22Z'}, id=None, type=<ResponseType.RESPONSE: 'response'>)
