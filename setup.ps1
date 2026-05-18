# XAUUSD Scalping Bot Setup
Write-Host "Setting up XAUUSD Scalping Bot..." -ForegroundColor Cyan

# Check Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "ERROR: Python not found. Install Python 3.8+ first." -ForegroundColor Red
    exit 1
}
Write-Host "Python found: $($python.Source)" -ForegroundColor Green

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt

# Verify MetaTrader5
python -c "import MetaTrader5; print('MetaTrader5 OK:', MetaTrader5.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: MetaTrader5 import failed. Ensure MT5 terminal is installed." -ForegroundColor Yellow
}

# Check credentials file
if (-not (Test-Path "mt5_credentials.json")) {
    Write-Host "ERROR: mt5_credentials.json not found. Copy the template and fill in your details." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Setup complete! Edit mt5_credentials.json with your account details, then run: python main.py" -ForegroundColor Green
