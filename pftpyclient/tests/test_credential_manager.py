from xrpl.wallet import Wallet
from pftpyclient.user_login.credentials import CredentialManager

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
    CredentialManager.cache_credentials(input_map)

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
    assert cred_manager.ecdh_public_key is None

    # Rederive and verify key is same after re-derivation
    cred_manager = CredentialManager(username, password)
    key3 = cred_manager.get_ecdh_public_key()
    assert key1 == key3

    # Delete credentials for test_user
    cred_manager.delete_credentials()
    assert username not in CredentialManager.get_cached_usernames()

    print("All tests passed")

if __name__ == "__main__":
    test_ecdh_key_derivation()
