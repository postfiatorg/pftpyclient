from setuptools import setup, find_packages
import os

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
    name='pftpyclient',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        'numpy',
        'pandas',
        'sqlalchemy',
        'cryptography',
        'xrpl-py',
        'wxPython',
        'requests',
        'toml',
        'nest_asyncio',
        'browser_history',
        'sec-cik-mapper',
        'loguru',
        'brotli',
    ],
    extras_require={
        'windows': ['pywin32'],
    },
    author='PFAdmin',
    author_email='admin@postfiat.com',
    description='Basic Post Fiat Python Functionality',
    long_description=read('README.md'),
    long_description_content_type='text/markdown',
    url='https://github.com/postfiatorg/pftpyclient',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.11',
    entry_points={
        'console_scripts': [
            'pft=pftpyclient.wallet_ux.prod_wallet:main',
            'pft-shortcut=pftpyclient.basic_utilities.create_shortcut:create_shortcut',
        ],
    },
    include_package_data=True,
    package_data={
        'pftpyclient': ['images/*'],
    },
)
