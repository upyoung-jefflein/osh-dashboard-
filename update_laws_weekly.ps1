# 職安法規觀測站 - 每週五法規 XML 更新腳本
# 由 Windows 工作排程器自動呼叫，或手動執行

$ProjectDir = "C:\Users\jefflein\Documents\osh-dashboard"
$LogFile    = "$ProjectDir\update_laws.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Tee-Object -FilePath $LogFile -Append
}

Log "===== 法規 XML 週更新開始 ====="
Set-Location $ProjectDir

# 下載 XML 並重新產生 HTML
Log "執行 osh_dashboard.py --fetch-xml ..."
$output = py osh_dashboard.py --fetch-xml -o docs/index.html 2>&1
$output | ForEach-Object { Log $_ }

if ($LASTEXITCODE -ne 0) {
    Log "錯誤：腳本執行失敗（exit $LASTEXITCODE），中止。"
    exit 1
}

# Git commit + push
$status = git status --porcelain docs/index.html
if ($status) {
    Log "偵測到 docs/index.html 變更，準備 commit..."
    git add docs/index.html law_data_auto.xml 2>$null
    git add docs/index.html
    $date = Get-Date -Format "yyyy-MM-dd"
    git commit -m "chore: weekly law XML update $date"
    git pull --rebase origin main
    git push
    Log "Push 完成。"
} else {
    Log "docs/index.html 無變更，略過 commit。"
}

Log "===== 完成 ====="
