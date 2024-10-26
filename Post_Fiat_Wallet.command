
#!/bin/bash
set -e

# Function to find and activate virtual environment
activate_venv() {
    possible_venv_paths=(
        "/Users/philkir/Development/pftest/bin/activate"
        "/Users/philkir/Development/pftpyclient/venv/bin/activate"
        "/Users/philkir/Development/pftpyclient/.venv/bin/activate"
        "/Users/philkir/Development/pftpyclient/pftest/bin/activate"
    )

    for venv_path in "${possible_venv_paths[@]}"; do
        if [ -f "$venv_path" ]; then
            echo "Activating virtual environment: $venv_path"
            source "$venv_path"
            return 0
        fi
    done

    echo "No virtual environment found. Using system Python."
    return 1
}

echo "Starting Post Fiat Wallet..."

# Try to activate virtual environment
activate_venv

# Run the application
python -m pftpyclient.wallet_ux.prod_wallet

echo "Post Fiat Wallet closed."
read -p "Press Enter to exit..."
