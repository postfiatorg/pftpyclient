from pftpyclient.basic_utilities import settings as gvst
import getpass
from pftpyclient.basic_utilities.settings import *
from cryptography.fernet import InvalidToken
from loguru import logger


class CredentialManager:
    def __init__(self,username,password):
        self.postfiat_username = username.lower()
        self.wallet_address_name = f'{self.postfiat_username}__v1xrpaddress'
        self.wallet_secret_name = f'{self.postfiat_username}__v1xrpsecret'
        self.google_doc_name = f'{self.postfiat_username}__googledoc'
        self.key_variables = [self.wallet_address_name, self.wallet_secret_name, 'postfiatusername']
        self.pw_initiator = password

        try:
            self.PW_MAP = self.output_fully_decrypted_cred_map(pw_decryptor=self.pw_initiator)
        except InvalidToken:
            raise ValueError("Invalid username or password")

        self.fields_that_need_definition = [i for i in self.key_variables if i not in self.PW_MAP.keys()]

    def output_fully_decrypted_cred_map(self, pw_decryptor):
        '''Your existing function here to decrypt the credential map.'''
        return output_fully_decrypted_cred_map(pw_decryptor)

    def output_cred_map(self):
        '''Your existing function here to output the credential map.'''
        return output_cred_map()


    def enter_and_encrypt_credential(self, credential_ref, pw_data, pw_encryptor):
        existing_cred_map = self.output_cred_map()

        if credential_ref in existing_cred_map.keys():
            logger.debug(f'Credential {credential_ref} is already loaded')
            logger.debug(f'To edit credential file directly go to {CREDENTIAL_FILE_PATH}')
            return

        credential_byte_str = pwl.password_encrypt(message=bytes(pw_data, 'utf-8'), password=pw_encryptor)

        fblock = f'''
variable___{credential_ref}
{credential_byte_str}'''

        with open(CREDENTIAL_FILE_PATH, 'a') as f:
            f.write(fblock)

        logger.debug(f"Added credential {credential_ref} to {CREDENTIAL_FILE_PATH}")

    def enter_and_encrypt_multiple_credentials(self):
        '''Allows entering multiple credentials with a single encryption password'''
        pw_encryptor = self.pw_initiator  # Use the same password for encryption

        # Encrypt and store the postfiatusername if needed
        if 'postfiatusername' not in self.PW_MAP:
            self.enter_and_encrypt_credential('postfiatusername', self.postfiat_username, pw_encryptor)

        # Encrypt and store other credentials
        for credential_ref in self.fields_that_need_definition:
            if credential_ref == 'postfiatusername':
                continue  # Skip since it's already handled
            pw_data = input(f'Enter your unencrypted credential for {credential_ref} (will be encrypted): ')
            self.enter_and_encrypt_credential(credential_ref, pw_data, pw_encryptor)

    def manage_credentials(self):
        '''Main method to manage the credentials'''
        if self.fields_that_need_definition:
            self.enter_and_encrypt_multiple_credentials()
        else:
            logger.debug("All required credentials are already loaded.")

