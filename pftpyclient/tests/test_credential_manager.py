from xrpl.wallet import Wallet
from pftpyclient.user_login.credential_input import cache_credentials, CredentialManager

def test_ecdh_key_derivation():
    # Setup test credentials
    username = 'test_user'
    password = 'test_password'
    wallet = Wallet.create()

    # Create credential manager with test wallet
    input_map = {
        "Username_Input": username,
        "Password_Input": password,
        "XRP Address_Input": wallet.classic_address,
        "XRP Secret_Input": wallet.seed
    }
    cache_credentials(input_map)

    # Initialize credential manager
    cred_manager = CredentialManager(username, password)

    # Get public key
    public_key = cred_manager.get_ecdh_public_key()

    # Verify its a valid ED25519 public key (33 bytes with ED prefix)
    assert len(bytes.fromhex(public_key)) == 33
    assert bytes.fromhex(public_key)[0] == 0xED

    # Test key consistency
    key1 = cred_manager.get_ecdh_public_key()
    key2 = cred_manager.get_ecdh_public_key()
    assert key1 == key2

    # Clear and assert key is no longer available
    cred_manager.clear_credentials()
    assert not hasattr(cred_manager, 'ecdh_public_key')

    # Rederive and verify key is same after re-derivation
    cred_manager = CredentialManager(username, password)
    key3 = cred_manager.get_ecdh_public_key()
    assert key1 == key3

if __name__ == "__main__":
    test_ecdh_key_derivation()
