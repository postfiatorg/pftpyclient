from pftpyclient.basic_utilities import settings as gvst
import getpass
from pftpyclient.basic_utilities.settings import *
from cryptography.fernet import InvalidToken
from loguru import logger
import shutil
import json

CREDENTIAL_FILENAME = "manyasone_cred_list.txt"

class CredentialManager:
    def __init__(self,username,password):
        self.postfiat_username = username.lower()
        self.wallet_address_name = f'{self.postfiat_username}__v1xrpaddress'
        self.wallet_secret_name = f'{self.postfiat_username}__v1xrpsecret'
        self.google_doc_name = f'{self.postfiat_username}__googledoc'
        self.contacts_name = f'{self.postfiat_username}__contacts'
        self.key_variables = [self.wallet_address_name, self.wallet_secret_name, self.google_doc_name, self.contacts_name]
        self.pw_initiator = password
        self.credential_file_path = get_credential_file_path()

        try:
            self.pw_map = self.decrypt_creds(pw_decryptor=self.pw_initiator)
        except InvalidToken:
            raise ValueError("Invalid username or password")

        self.fields_that_need_definition = [i for i in self.key_variables if i not in self.pw_map.keys()]

    def decrypt_creds(self, pw_decryptor):
        '''Decrypts all credentials in the file'''
        encrypted_cred_map = _get_cred_map()

        decrypted_cred_map = {
            self.wallet_address_name: pwl.password_decrypt(token=encrypted_cred_map[self.wallet_address_name], password=pw_decryptor).decode('utf-8'),
            self.wallet_secret_name: pwl.password_decrypt(token=encrypted_cred_map[self.wallet_secret_name], password=pw_decryptor).decode('utf-8'),
        }

        # Only add google_doc_name if it exists in the encrypted_cred_map
        if self.google_doc_name in encrypted_cred_map:
            decrypted_cred_map[self.google_doc_name] = pwl.password_decrypt(
                token=encrypted_cred_map[self.google_doc_name], 
                password=pw_decryptor
            ).decode('utf-8')

        # Only add contacts if they exist in the encrypted_cred_map
        if self.contacts_name in encrypted_cred_map:
            decrypted_cred_map[self.contacts_name] = pwl.password_decrypt(
                token=encrypted_cred_map[self.contacts_name], 
                password=pw_decryptor
            ).decode('utf-8')
        
        return decrypted_cred_map 
    
    def enter_and_encrypt_credential(self, credentials_dict):
        """Encryps and stores multiple credentials"""
        enter_and_encrypt_credential(credentials_dict=credentials_dict, pw_encryptor=self.pw_initiator)

    def change_password(self, current_password, new_password):
        """Change the encryption password for the current user's credentials"""
        # Verify current password
        try:
            current_creds = self.decrypt_creds(current_password)
        except InvalidToken:
            return ValueError("Current password is incorrect")
        
        # Create backup
        backup_path = self.credential_file_path.with_suffix('.txt_backup')
        shutil.copy2(self.credential_file_path, backup_path)

        try:
            # Get all existing credentials
            existing_cred_map = _get_cred_map()

            # Prepare new credentials for current user
            new_creds = []
            for key in self.key_variables:
                if key in current_creds:
                    credential_byte_str = pwl.password_encrypt(
                        message=bytes(current_creds[key], 'utf-8'), 
                        password=new_password
                    )
                    new_creds.append(f'\nvariable___{key}\n{credential_byte_str}')

            # Write updated credential file
            with open(self.credential_file_path, 'w') as f:
                # Write credentials for other users (unchanged)
                for key, value in existing_cred_map.items():
                    if key not in self.key_variables:
                        f.write(f'\nvariable___{key}\n{value}')
                # Write new credentials for current user
                f.write(''.join(new_creds))

            # Update instance password
            self.pw_initiator = new_password

            # Remove backup file after successful change
            if backup_path.exists():
                os.remove(backup_path)

            return True
        
        except Exception as e:
            # Restore from backup if anything fails
            if backup_path.exists():
                shutil.copy2(backup_path, self.credential_file_path)
                os.remove(backup_path)
            logger.error(f"Error changing password: {e}")
            raise Exception(f"Error changing password: {e}")
        
    def clear_credentials(self):
        """Securely clear all credentials from memory"""
        try:
            # Clear decrypted credentials
            if hasattr(self, 'pw_map'):
                for key in self.pw_map:
                    self.pw_map[key] = '0' * len(self.pw_map[key])  # overwrite with zeros
                self.pw_map.clear()
                del self.pw_map

            # Clear encryption password
            if hasattr(self, 'pw_initiator'):
                self.pw_initiator = '0' * len(self.pw_initiator)  # overwrite with zeros
                del self.pw_initiator

            # Clear other sensitive data
            self.postfiat_username = None
            self.wallet_address_name = None
            self.wallet_secret_name = None
            self.google_doc_name = None
            self.key_variables = None

            logger.debug("Credentials cleared from memory")

        except Exception as e:
            logger.error(f"Error clearing credentials: {e}")
            raise Exception(f"Error clearing credentials: {e}")
        
    def delete_credentials(self):
        """Delete all credentials for the current user"""
        try:
            # Get all existing credentials
            existing_cred_map = _get_cred_map()

            # Create backup
            backup_path = self.credential_file_path.with_suffix('.txt_backup')
            shutil.copy2(self.credential_file_path, backup_path)

            try:
                # Write updated credential file without current user's credentials
                with open(self.credential_file_path, 'w') as f:
                    for key, value in existing_cred_map.items():
                        if key not in self.key_variables:
                            f.write(f'\nvariable___{key}\n{value}')

                # Clear credentials from memory
                self.clear_credentials()

                # Remove backup file after successful deletion
                if backup_path.exists():
                    os.remove(backup_path)

                logger.info(f"Successfully deleted account for user: {self.postfiat_username}")
                return True
            
            except Exception as e:
                # Restore from backup if anything fails
                if backup_path.exists():
                    shutil.copy2(backup_path, self.credential_file_path)
                    os.remove(backup_path)
                logger.error(f"Error deleting account: {e}")
                raise Exception(f"Error deleting account: {e}")
            
        except Exception as e:
            logger.error(f"Error deleting account: {e}")
            raise Exception(f"Error deleting account: {e}")
        
    def get_contacts(self):
        """Returns dictionary of contacts or empty dict if none exist"""
        try:
            if self.contacts_name in self.pw_map:
                contacts_json = self.pw_map[self.contacts_name]
                return json.loads(contacts_json)
            return {}
        except Exception as e:
            logger.error(f"Error getting contacts: {e}")
            return {}
        
    def save_contact(self, address: str, name: str):
        """Save or update a contact"""
        contacts = self.get_contacts()
        contacts[address] = name
        self._save_contacts(contacts)

    def delete_contact(self, address: str):
        """Delete a contact by address"""
        contacts = self.get_contacts()
        if address in contacts:
            del contacts[address]
            self._save_contacts(contacts)

    def _save_contacts(self, contacts: dict):
        """Save contacts to the credential file"""
        contacts_json = json.dumps(contacts)
        enter_and_encrypt_credential(credentials_dict={self.contacts_name: contacts_json}, pw_encryptor=self.pw_initiator)
        self.pw_map = self.decrypt_creds(pw_decryptor=self.pw_initiator)
    
def _read_creds(credential_file_path):
    with open(credential_file_path, 'r') as f:
        credblock = f.read()
    return credblock

def _convert_credential_string_to_map(stringx):
    '''Converts a credential string to a map'''
    def convert_string_to_bytes(string):
        if string.startswith("b'"):
            return bytes(string[2:-1], 'utf-8')
        else:
            return string
    
    variables = re.findall(r'variable___\w+', stringx)
    map_constructor = {}
    
    for variable_to_work in variables:
        raw_text = stringx.split(variable_to_work)[1].split('variable___')[0].strip()
        variable_name = variable_to_work.split('variable___')[1]
        map_constructor[variable_name] = convert_string_to_bytes(string=raw_text)
    
    return map_constructor
    
def _get_cred_map():
    credblock = _read_creds(get_credential_file_path())
    return _convert_credential_string_to_map(credblock)   
    
def enter_and_encrypt_credential(credentials_dict, pw_encryptor):
    """
    Encrypt and store multiple credentials.

    :param credentials_dict: Dictionary of credential references and their values
    :param pw_encryptor: Password used for encryption
    """
    
    existing_cred_map = _get_cred_map()
    new_credentials = []
    
    for credential_ref, pw_data in credentials_dict.items():
        # Allow updates for contacts
        if credential_ref in existing_cred_map.keys() and not credential_ref.endswith('__contacts'):
            logger.error(f'Credential {credential_ref} is already loaded')
            return
        
        credential_byte_str = pwl.password_encrypt(message=bytes(pw_data, 'utf-8'), password=pw_encryptor)
        
        new_credentials.append(f'\nvariable___{credential_ref}\n{credential_byte_str}')
    
    if new_credentials:
        # For contacts, we need to overwrite the existing entry
        if any(ref.endswith('__contacts') for ref in credentials_dict.keys()):
            with open(get_credential_file_path(), 'r') as f:
                lines = f.readlines()

            # Remove existing contacts entry
            contact_key = next(ref for ref in credentials_dict.keys() if ref.endswith('__contacts'))
            lines = [line for line in lines if not (contact_key in line)]

            # Write back all lines except contacts
            with open(get_credential_file_path(), 'w') as f:
                f.writelines(lines)
                f.write(''.join(new_credentials))
        else:
            # For non-contacts credentials, append as usual
            with open(get_credential_file_path(), 'a') as f:
                f.write(''.join(new_credentials))
            
        logger.debug(f"Added {len(new_credentials)} new credentials to {get_credential_file_path()}")
    else:
        logger.debug("No new credentials to add")

def cache_credentials(input_map):
    """
    Cache user credentials locally.
    
    :param input_map: Dictionary containing user credentials
    :return: String message indicating the result of the operation
    """
    try: 
        credentials = {
            f'{input_map["Username_Input"]}__v1xrpaddress': input_map['XRP Address_Input'],
            f'{input_map["Username_Input"]}__v1xrpsecret': input_map['XRP Secret_Input'],
            # f'{input_map["Username_Input"]}__googledoc': input_map['Google Doc Share Link_Input']                
        }

        enter_and_encrypt_credential(
            credentials_dict=credentials,
            pw_encryptor=input_map['Password_Input']
        )

        return f'Information Cached and Encrypted Locally Using Password to {get_credential_file_path()}'
    
    except Exception as e:
        logger.error(f"Error caching credentials: {e}")
        return f"Error caching credentials: {e}"

def get_credentials_directory():
    '''Returns the path to the postfiatcreds directory, creating it if it does not exist'''
    creds_dir = Path.home().joinpath("postfiatcreds")
    creds_dir.mkdir(exist_ok=True)
    return creds_dir

def get_credential_file_path():
    '''Returns the path to the credential file, creating it if it does not exist'''
    creds_dir = get_credentials_directory()
    cred_file_path = creds_dir / CREDENTIAL_FILENAME
    
    if not cred_file_path.exists():
        cred_file_path.touch()
        logger.info(f"Created credentials file at {cred_file_path}")
    
    return cred_file_path

def get_cached_usernames():
    '''Returns a list of unique usernames from cached credentials'''
    try:
        cred_map = _get_cred_map()
        # Extract unique usernames from credential keys (removing the suffixes)
        usernames = set()
        for key in cred_map.keys():
            if '__v1xrpaddress' in key:  # Use wallet address as indicator of username
                username = key.replace('__v1xrpaddress', '')
                usernames.add(username)
        return sorted(list(usernames))
    except Exception as e:
        logger.error(f"Error reading cached usernames: {e}")
        return []