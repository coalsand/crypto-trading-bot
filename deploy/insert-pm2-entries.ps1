# One-time helper: inserts the two crypto PM2 entries into ecosystem.config.js
# before the nginx entry. Idempotent: does nothing if entries already present.

$eco = 'C:\Users\BrianChan\Documents\ecosystem.config.js'
$content = Get-Content $eco -Raw

if ($content -match "crypto-trading-web") {
    Write-Host "PM2 entries already present. Nothing to do."
    exit 0
}

$newEntries = @"
    {
      name: 'crypto-trading-web',
      script: 'venv\\Scripts\\python.exe',
      args: 'run_web.py --host 0.0.0.0 --port 5003',
      cwd: 'C:\\Users\\BrianChan\\Documents\\crypto_trading_bot',
      out_file: 'C:\\Users\\BrianChan\\Documents\\pm2-logs\\crypto-web-out.log',
      error_file: 'C:\\Users\\BrianChan\\Documents\\pm2-logs\\crypto-web-err.log',
      autorestart: true, watch: false, windowsHide: true,
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    },
    {
      name: 'crypto-trading-scheduler',
      script: 'venv\\Scripts\\python.exe',
      args: '-m crypto_trading_bot.main --paper',
      cwd: 'C:\\Users\\BrianChan\\Documents\\crypto_trading_bot',
      out_file: 'C:\\Users\\BrianChan\\Documents\\pm2-logs\\crypto-scheduler-out.log',
      error_file: 'C:\\Users\\BrianChan\\Documents\\pm2-logs\\crypto-scheduler-err.log',
      autorestart: true, watch: false, windowsHide: true,
      env: { PYTHONPATH: 'C:\\Users\\BrianChan\\Documents' },
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    },
"@

$anchor = "    {`r`n      name: 'nginx',"
if ($content -notmatch [regex]::Escape($anchor)) {
    Write-Error "Anchor not found: nginx entry. ecosystem.config.js format may have changed."
    exit 1
}
$replacement = $newEntries + "`r`n" + $anchor
$newContent = $content -replace [regex]::Escape($anchor), $replacement
Set-Content -Path $eco -Value $newContent -NoNewline
Write-Host "Inserted crypto PM2 entries."
