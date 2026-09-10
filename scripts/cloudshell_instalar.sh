# ============================================================================
# PASO 1 — instalar el programa de trabajadores en el EC2 (pegar ENTERO en CloudShell)
#
# Qué hace en el server: baja instalar.ps1 y prender_auto_update.ps1 del repo,
# arma la conexión a la base con las MISMAS variables DB_* que ya usa Programa
# Core (no viaja ninguna clave por acá), inventa una SECRET_KEY y la clave del
# usuario admin, instala la app en C:\trabajadores_app (puerto 5005), registra
# la tarea TrabajadoresApp, verifica /healthz y deja el auto-update tirando del
# repo cada 2 minutos.
#
# La clave del usuario «admin» se IMPRIME al final, acá en CloudShell. Anotala.
# ============================================================================
export AWS_PAGER=""
python3 - <<'PY' > /tmp/trab_instalar.json
import json
ps = r'''
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$cb = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$raw = "https://raw.githubusercontent.com/t-eliscovich/intela-trabajadores/main/scripts"
New-Item -ItemType Directory -Path C:\trabajadores_update -Force | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri "$raw/instalar.ps1?cb=$cb" -OutFile C:\trabajadores_update\instalar.ps1 -Headers @{ 'Cache-Control' = 'no-cache' }
Invoke-WebRequest -UseBasicParsing -Uri "$raw/prender_auto_update.ps1?cb=$cb" -OutFile C:\trabajadores_update\prender_auto_update.ps1 -Headers @{ 'Cache-Control' = 'no-cache' }

# La base: las mismas DB_* de Programa Core (variables de maquina o su .env).
function LeerVar($n) {
  $v = [Environment]::GetEnvironmentVariable($n, "Machine")
  if (-not $v -and (Test-Path C:\programa-core\.env)) {
    $l = Get-Content C:\programa-core\.env | Where-Object { $_ -match "^$n=" } | Select-Object -First 1
    if ($l) { $v = $l.Substring($n.Length + 1).Trim().Trim('"').Trim("'") }
  }
  return $v
}
$h = LeerVar "DB_HOST"; $d = LeerVar "DB_NAME"; $u = LeerVar "DB_USER"; $p = LeerVar "DB_PASSWORD"; $port = LeerVar "DB_PORT"
if (-not $port) { $port = "5432" }
if (-not ($h -and $d -and $u -and $p)) { throw "no encontre DB_HOST/DB_NAME/DB_USER/DB_PASSWORD de Programa Core" }
$pEnc = [Uri]::EscapeDataString($p)
$url = "postgresql://${u}:${pEnc}@${h}:${port}/${d}?sslmode=require"

function Azar($n) { -join ((48..57 + 65..90 + 97..122) | Get-Random -Count $n | ForEach-Object { [char]$_ }) }
$secret = Azar 48
$admin  = Azar 12

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\trabajadores_update\instalar.ps1 -DatabaseUrl $url -SecretKey $secret -AdminInicial $admin
if ($LASTEXITCODE -ne 0) { throw "instalar.ps1 salio con $LASTEXITCODE" }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\trabajadores_update\prender_auto_update.ps1
Write-Output "=================================================="
Write-Output "LISTO. Usuario de contabilidad: admin   Clave: $admin"
Write-Output "Base: ${h}/${d} como ${u}"
Write-Output "=================================================="
'''
print(json.dumps({"commands": [ps]}))
PY

ID=$(aws ssm send-command --region us-east-2 --instance-ids i-0fcca4d7029f08489 \
     --document-name AWS-RunPowerShellScript --parameters file:///tmp/trab_instalar.json \
     --timeout-seconds 600 --query 'Command.CommandId' --output text)
echo "comando $ID — esperando 150 s (pip + arranque)..."
sleep 150
aws ssm get-command-invocation --region us-east-2 --instance-id i-0fcca4d7029f08489 \
  --command-id "$ID" --query '{Status:Status,Out:StandardOutputContent,Err:StandardErrorContent}' --output json
# Si Status es InProgress, repetir sólo la última línea (get-command-invocation) hasta que termine.
