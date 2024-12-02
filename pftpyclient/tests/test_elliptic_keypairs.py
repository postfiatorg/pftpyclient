from xrpl.core.keypairs.ed25519 import ED25519
from xrpl.core import addresscodec
from xrpl.wallet import Wallet
from pftpyclient.postfiatsecurity.hash_tools import derive_shared_secret, get_account_id

# ED25519

# Party A
# 1. Generate keypair
wallet_a = Wallet.create()
wallet_a_address = wallet_a.classic_address
decoded_seed_a = addresscodec.decode_seed(wallet_a.seed)
raw_entropy_a = decoded_seed_a[0]
public_key_raw_a, private_key_raw_a = ED25519.derive_keypair(raw_entropy_a, is_validator=False)

# Party B
# 1. Generate keypair
wallet_b = Wallet.create()
wallet_b_address = wallet_b.classic_address
decoded_seed_b = addresscodec.decode_seed(wallet_b.seed)
raw_entropy_b = decoded_seed_b[0]
public_key_raw_b, private_key_raw_b = ED25519.derive_keypair(raw_entropy_b, is_validator=False)

# 3. Confirm public keys derive to the expected XRP address
wallet_a_confirming_address_b = addresscodec.encode_classic_address(get_account_id(public_key_raw_b))
wallet_b_confirming_address_a = addresscodec.encode_classic_address(get_account_id(public_key_raw_a))

assert wallet_a_confirming_address_b == wallet_b_address
assert wallet_b_confirming_address_a == wallet_a_address

# 4. Both parties can now derive the same shared secret
# Party A derives
shared_secret_a = derive_shared_secret(public_key_raw_b, raw_entropy_a)

# Party B derives
shared_secret_b = derive_shared_secret(public_key_raw_a, raw_entropy_b)

# Confirm both shared secrets are the same
assert shared_secret_a == shared_secret_b