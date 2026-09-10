# ============================================================================
# PASO 2 — publicar trabajadores.intela.com.ec en Caddy (pegar ENTERO en CloudShell)
# SÓLO después de que el registro DNS exista (dig trabajadores.intela.com.ec).
# ============================================================================
export AWS_PAGER=""
python3 - <<'PY' > /tmp/trab_caddy.json
import json
ps = r'''
$ErrorActionPreference = "Stop"
$f = "C:\caddy\Caddyfile"
$c = Get-Content $f -Raw
if ($c -notmatch "trabajadores\.intela\.com\.ec") {
  Copy-Item $f "$f.bak-$(Get-Date -Format yyyyMMdd-HHmm)"
  Add-Content $f "`r`ntrabajadores.intela.com.ec {`r`n    reverse_proxy 127.0.0.1:5005`r`n}`r`n"
  Write-Output "bloque agregado"
} else { Write-Output "ya estaba" }
Restart-Service CaddyService
Start-Sleep 20
try { (Invoke-WebRequest -UseBasicParsing https://trabajadores.intela.com.ec/healthz -TimeoutSec 30).Content }
catch { Write-Output "todavia no contesta por https: $($_.Exception.Message) (Caddy pide el certificado; probar de nuevo en 1 min)" }
'''
print(json.dumps({"commands": [ps]}))
PY

ID=$(aws ssm send-command --region us-east-2 --instance-ids i-0fcca4d7029f08489 \
     --document-name AWS-RunPowerShellScript --parameters file:///tmp/trab_caddy.json \
     --query 'Command.CommandId' --output text)
sleep 45
aws ssm get-command-invocation --region us-east-2 --instance-id i-0fcca4d7029f08489 \
  --command-id "$ID" --query '{Status:Status,Out:StandardOutputContent,Err:StandardErrorContent}' --output json
