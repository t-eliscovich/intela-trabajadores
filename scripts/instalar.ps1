# instalar.ps1 — deja el programa andando en el server POR PRIMERA VEZ.
#
# Se corre UNA sola vez (por SSM desde CloudShell, o en una consola del EC2).
# Despues de esto todo entra por GitHub: prender_auto_update.ps1 deja al
# server tirando del repo cada 2 minutos.
#
# Que hace:
#   1. Guarda las variables de MAQUINA (base, clave, puerto, admin inicial).
#   2. Baja main del repo a C:\trabajadores_app e instala requirements.
#   3. Registra la tarea programada TrabajadoresApp (python launch.py, SYSTEM,
#      sin limite de tiempo, se relanza sola) y la arranca.
#   4. Verifica /healthz en el puerto 5005.
#
# Uso (los tres primeros son obligatorios):
#   .\instalar.ps1 -DatabaseUrl "postgresql://usuario:clave@host:5432/intela?sslmode=require" `
#                  -SecretKey "<algo largo y al azar>" -AdminInicial "<clave del usuario admin>"
#
# La base es la MISMA del cluster RDS que usa Programa Core (db "intela");
# el programa crea su schema `trabajadores` solo al arrancar.
#
# Idempotente: se puede volver a correr; re-registra la tarea y vuelve a bajar.

param(
    [Parameter(Mandatory=$true)][string]$DatabaseUrl,
    [Parameter(Mandatory=$true)][string]$SecretKey,
    [Parameter(Mandatory=$true)][string]$AdminInicial,
    [int]$Puerto = 5005
)
$ErrorActionPreference = "Stop"
$repo   = "t-eliscovich/intela-trabajadores"
$app    = "C:\trabajadores_app"
$tarea  = "TrabajadoresApp"
$python = "C:\Python312\python.exe"

# 1. Variables de maquina. launch.py las lee del registro al arrancar.
[Environment]::SetEnvironmentVariable("TRABAJADORES_DATABASE_URL",   $DatabaseUrl,  "Machine")
[Environment]::SetEnvironmentVariable("TRABAJADORES_SECRET_KEY",     $SecretKey,    "Machine")
[Environment]::SetEnvironmentVariable("TRABAJADORES_PORT",           "$Puerto",     "Machine")
[Environment]::SetEnvironmentVariable("TRABAJADORES_ADMIN_INICIAL",  $AdminInicial, "Machine")
Write-Output "variables guardadas"

# 2. Bajar el codigo.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$tmp = Join-Path $env:TEMP ("trab_inst_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 `
    -Uri "https://codeload.github.com/$repo/tar.gz/refs/heads/main" -OutFile "$tmp\src.tar.gz"
tar -xzf "$tmp\src.tar.gz" -C $tmp
$nuevo = Get-ChildItem $tmp -Directory | Where-Object { $_.Name -like "intela-trabajadores-*" } | Select-Object -First 1
if (-not $nuevo -or -not (Test-Path "$($nuevo.FullName)\app.py")) { throw "descarga incompleta" }
$sha = (Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/$repo/commits/main" `
        -Headers @{ 'User-Agent' = 'trabajadores-instalar' }).sha
Set-Content -Path "$($nuevo.FullName)\.version" -Value $sha -Encoding ASCII

Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
foreach ($pid_ in @(Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue |
                    Select-Object -ExpandProperty OwningProcess -Unique)) {
    Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
}
if (Test-Path $app) { Remove-Item $app -Recurse -Force }
Move-Item $nuevo.FullName $app
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Output "codigo en $app ($($sha.Substring(0,8)))"

# pip escribe avisos en stderr; lo que importa es el codigo de salida.
$ErrorActionPreference = "Continue"
& $python -m pip install --quiet --disable-pip-version-check -r "$app\requirements.txt" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pip salio con codigo $LASTEXITCODE" }
$ErrorActionPreference = "Stop"

# 3. La tarea programada. Mismos ajustes que las otras apps del box: sin limite
#    de 72 h (si no, el Programador la mata al tercer dia) y con reintentos.
$accion  = New-ScheduledTaskAction -Execute $python -Argument "launch.py" -WorkingDirectory $app
$gatillo = New-ScheduledTaskTrigger -AtStartup
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$quien = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Unregister-ScheduledTask -TaskName $tarea -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $tarea -Action $accion -Trigger $gatillo -Settings $ajustes -Principal $quien | Out-Null
Start-ScheduledTask -TaskName $tarea
Write-Output "tarea $tarea registrada y arrancada"

# 4. Que conteste.
$ok = $false
foreach ($i in 1..10) {
    Start-Sleep 6
    try {
        $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Puerto/healthz" -TimeoutSec 10
        if ($r.StatusCode -eq 200) { $ok = $true; Write-Output $r.Content; break }
    } catch { }
}
if (-not $ok) {
    Write-Output "NO contesta. Ultimas lineas del log:"
    Get-ChildItem "$app\logs" -Filter "*.log" -EA SilentlyContinue | Sort-Object LastWriteTime |
        Select-Object -Last 1 | ForEach-Object { Get-Content $_.FullName -Tail 30 }
    throw "la app no levanto"
}
Write-Output "OK. Ahora: .\prender_auto_update.ps1 (para que tire del repo solo) y el bloque de Caddy."
