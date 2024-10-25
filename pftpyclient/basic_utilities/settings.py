import os
import re
import datetime
import glob
from platform import system
from pathlib import Path
from pftpyclient.postfiatsecurity import hash_tools as pwl
from loguru import logger

def datetime_current_EST():
    '''EST should be used for all timestamps'''
    now = datetime.datetime.now()
    eastern_time = now.astimezone(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=-5)))
    return eastern_time

def get_datadump_directory_path():
    '''Returns the path to the datadump directory, creating it if it does not exist'''
    home_dir = Path.home()
    datadump_dir = home_dir / "datadump"
    data_dir = datadump_dir / "data"
    
    datadump_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    
    return datadump_dir

# TODO: Change this from constant to variable
DATADUMP_DIRECTORY_PATH = get_datadump_directory_path()

def convert_directory_tuple_to_filename(directory_tuple):
    '''Converts a tuple of directory paths to a single path string'''
    string_list = []
    for item in directory_tuple:
        if isinstance(item, list):
            string_list.extend(item)
        else:
            string_list.append(item)
    
    return '/'.join(string_list)

# def convert_credential_string_to_map(stringx):
#     '''Converts a credential string to a map'''
#     def convert_string_to_bytes(string):
#         if string.startswith("b'"):
#             return bytes(string[2:-1], 'utf-8')
#         else:
#             return string
    
#     variables = re.findall(r'variable___\w+', stringx)
#     map_constructor = {}
    
#     for variable_to_work in variables:
#         raw_text = stringx.split(variable_to_work)[1].split('variable___')[0].strip()
#         variable_name = variable_to_work.split('variable___')[1]
#         map_constructor[variable_name] = convert_string_to_bytes(string=raw_text)
    
#     return map_constructor

# def read_creds():
#     with open(CREDENTIAL_FILE_PATH, 'r') as f:
#         credblock = f.read()
#     return credblock

# def output_cred_map():
#     credblock = read_creds()
#     cred_map = convert_credential_string_to_map(credblock)
#     return cred_map

# def enter_and_encrypt_credential():
#     credential_ref = input('Enter your credential reference (example: aws_key): ')
#     existing_cred_map = output_cred_map()
    
#     if credential_ref in existing_cred_map.keys():
#         print('Credential is already loaded')
#         print(f'To edit credential file directly go to {CREDENTIAL_FILE_PATH}')
#         return
    
#     pw_data = input('Enter your unencrypted credential (will be encrypted next step): ')
#     pw_encryptor = input('Enter your encryption password: ')
    
#     credential_byte_str = pwl.password_encrypt(message=bytes(pw_data, 'utf-8'), password=pw_encryptor)
    
#     fblock = f'''
# variable___{credential_ref}
# {credential_byte_str}'''
    
#     with open(CREDENTIAL_FILE_PATH, 'a') as f:
#         f.write(fblock)
    
#     print(f"Added credential {credential_ref} to {CREDENTIAL_FILE_PATH}")

# def enter_and_encrypt_credential__variable_based(credentials_dict, pw_encryptor):
#     """
#     Encrypt and store multiple credentials.

#     :param credentials_dict: Dictionary of credential references and their values
#     :param pw_encryptor: Password used for encryption
#     """
    
#     existing_cred_map = output_cred_map()
#     new_credentials = []
    
#     for credential_ref, pw_data in credentials_dict.items():
#         if credential_ref in existing_cred_map.keys():
#             logger.error(f'Credential {credential_ref} is already loaded')
#             return
        
#         credential_byte_str = pwl.password_encrypt(message=bytes(pw_data, 'utf-8'), password=pw_encryptor)
        
#         new_credentials.append(f'\nvariable___{credential_ref}\n{credential_byte_str}')
    
#     if new_credentials:
#         with open(CREDENTIAL_FILE_PATH, 'a') as f:
#             f.write(''.join(new_credentials))
        
#         logger.debug(f"Added {len(new_credentials)} new credentials to {CREDENTIAL_FILE_PATH}")
#     else:
#         logger.debug("No new credentials to add")

# def decrypt_credential(credential_ref, pw_decryptor):
#     '''Decrypts a credential'''
#     existing_cred_map = output_cred_map()
    
#     if credential_ref in existing_cred_map.keys():
#         encrypted_cred = existing_cred_map[credential_ref]
#         decrypted_cred = pwl.password_decrypt(token=encrypted_cred, password=pw_decryptor)
#         return decrypted_cred.decode('utf-8')
#     else:
#         raise ValueError('Credential not found')

# def decrypt_credential(credential_ref, pw_decryptor):
#     '''Decrypts a credential'''
#     existing_cred_map = output_cred_map()
    
#     if credential_ref in existing_cred_map.keys():
#         encrypted_cred = existing_cred_map[credential_ref]
#         decrypted_cred = pwl.password_decrypt(token=encrypted_cred, password=pw_decryptor)
#         return decrypted_cred.decode('utf-8')
#     else:
#         raise ValueError('Credential not found')

# def output_fully_decrypted_cred_map(pw_decryptor):
#     '''Decrypts all credentials in the file'''
#     existing_cred_map = output_cred_map()
#     decrypted_cred_map = {}
    
#     for credential_ref in existing_cred_map.keys():
#         decrypted_cred_map[credential_ref] = decrypt_credential(credential_ref=credential_ref, pw_decryptor=pw_decryptor)
    
#     return decrypted_cred_map
