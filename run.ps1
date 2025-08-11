# run.ps1 - Windows PowerShell runner (uses backticks for line continuation)
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:TELEGRAM_BOT_TOKEN = "<YOUR_NEW_SECURE_TOKEN>"
$env:TELEGRAM_CHAT_ID   = "@yourchannelname"   # or -1001234567890
$env:POLL_SECONDS       = "900"
$env:X_USER             = "eWhispers"

python .\ew_link_bot.py
