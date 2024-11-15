DEFAULT_NODE = "r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD"
REMEMBRANCER_ADDRESS = "rJ1mBMhEBKack5uTQvM8vWoAntbufyG9Yn"
ISSUER_ADDRESS = "rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW"
TREASURY_WALLET_ADDRESS = "r46SUhCzyGE4KwBnKQ6LmDmJcECCqdKy4q"

SPECIAL_ADDRESSES = {
    REMEMBRANCER_ADDRESS: {
        "memo_pft_requirement": 1,
        "display_text": "Post Fiat Network Remembrancer"
    },
    ISSUER_ADDRESS: {
        "memo_pft_requirement": 0,
        "display_text": "Post Fiat Token Issuer"
    }
}

MAINNET_WEBSOCKETS = [
    "wss://xrplcluster.com",
    "wss://xrpl.ws/",
    "wss://s1.ripple.com/",
    "wss://s2.ripple.com/"
]
TESTNET_WEBSOCKETS = [
    "wss://s.altnet.rippletest.net:51233"
]
MAINNET_URL = "https://s2.ripple.com:51234"
TESTNET_URL = "https://s.altnet.rippletest.net:51234"

CREDENTIAL_FILENAME = "manyasone_cred_list.txt"

