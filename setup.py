from setuptools import setup, find_packages

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
        'sec-cik-mapper'
    ],
    author='PFAdmin',
    author_email='admin@postfiat.com',
    description='Basic Post Fiat Python Functionality',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/postfiatorg/pftpyclient',  # Replace with your actual GitHub repo URL
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.11',
)
