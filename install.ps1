# TeamScribe — installation guidée pour Windows.
#
# Conçu pour un nouvel utilisateur qui n'a jamais touché au projet : à chaque
# étape, on explique ce qui se passe et on demande confirmation avant d'agir.
# Aucune élévation admin n'est requise (tout reste en mode utilisateur).
#
# Utilisation : clic droit sur ce fichier -> "Exécuter avec PowerShell"
# (ou, dans un terminal PowerShell ouvert dans ce dossier : .\install.ps1)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Write-Title($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

function Write-Info($text) {
    Write-Host $text -ForegroundColor Gray
}

function Write-Ok($text) {
    Write-Host "[OK] $text" -ForegroundColor Green
}

function Read-YesNo($question, [bool]$defaultYes = $true) {
    $hint = if ($defaultYes) { "[O/n]" } else { "[o/N]" }
    $answer = Read-Host "$question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $defaultYes }
    return $answer.Trim().ToLower().StartsWith("o")
}

function Get-EnvValue($key) {
    if (-not (Test-Path ".env")) { return $null }
    $line = Get-Content ".env" | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -First 1
    if ($line) { return ($line -split "=", 2)[1].Trim() }
    return $null
}

function Set-EnvValue($key, $value) {
    $lines = @()
    if (Test-Path ".env") { $lines = Get-Content ".env" }
    $found = $false
    $newLines = foreach ($line in $lines) {
        if ($line -match "^\s*$key\s*=") {
            $found = $true
            "$key=$value"
        } else {
            $line
        }
    }
    if (-not $found) { $newLines += "$key=$value" }
    Set-Content -Path ".env" -Value $newLines -Encoding utf8
}

# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Bienvenue ! Ce script va installer TeamScribe sur ton ordinateur," -ForegroundColor White
Write-Host "étape par étape. Tu peux répondre 'o' (oui) ou 'n' (non) à chaque" -ForegroundColor White
Write-Host "question — appuie simplement sur Entrée pour accepter le choix par" -ForegroundColor White
Write-Host "défaut (en majuscule)." -ForegroundColor White

# --- 1. Python ---------------------------------------------------------

Write-Title "Étape 1/6 — Vérification de Python"

$python = Get-Command python -ErrorAction SilentlyContinue
$pythonOk = $false
if ($python) {
    $versionText = (& python --version) 2>&1
    if ($versionText -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
            $pythonOk = $true
            Write-Ok "Python détecté : $versionText"
        }
    }
}

if (-not $pythonOk) {
    Write-Host ""
    Write-Host "Python 3.10 ou plus récent n'a pas été détecté sur cet ordinateur." -ForegroundColor Yellow
    Write-Host "TeamScribe en a besoin pour fonctionner." -ForegroundColor Yellow
    if (Read-YesNo "Veux-tu que j'ouvre la page de téléchargement de Python ?") {
        Start-Process "https://www.python.org/downloads/"
    }
    Write-Host ""
    Write-Host "Installe Python (coche bien 'Add python.exe to PATH' pendant" -ForegroundColor Yellow
    Write-Host "l'installation), puis relance ce script (install.ps1)." -ForegroundColor Yellow
    Read-Host "Appuie sur Entrée pour fermer"
    exit 1
}

# --- 2. Dépendances ------------------------------------------------------

Write-Title "Étape 2/6 — Installation des composants TeamScribe"
Write-Info "Ceci télécharge et installe les librairies nécessaires (capture audio,"
Write-Info "transcription, interface) dans ton compte utilisateur — pas besoin"
Write-Info "de droits administrateur. Ça peut prendre quelques minutes."

$hasGpu = Read-YesNo "As-tu une carte graphique NVIDIA dédiée (pour une transcription plus rapide) ?" $false

Write-Host ""
Write-Info "Installation en cours…"
if ($hasGpu) {
    python -m pip install --user -e ".[gpu]"
} else {
    python -m pip install --user -e .
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "L'installation a échoué — copie le message d'erreur ci-dessus et" -ForegroundColor Red
    Write-Host "envoie-le à la personne qui t'a partagé ce projet." -ForegroundColor Red
    Read-Host "Appuie sur Entrée pour fermer"
    exit 1
}
Write-Ok "Composants installés."

# --- 3. Clé Anthropic (résumés) ------------------------------------------

Write-Title "Étape 3/6 — Clé Anthropic (pour les résumés automatiques)"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

$existingKey = Get-EnvValue "ANTHROPIC_API_KEY"
$needsKey = -not $existingKey -or $existingKey -like "sk-ant-xxx*"

if ($needsKey) {
    Write-Info "TeamScribe utilise Claude (Anthropic) pour résumer tes réunions."
    Write-Info "Il te faut TA PROPRE clé — chaque personne a la sienne, avec ses"
    Write-Info "propres crédits. Tu peux en créer une sur console.anthropic.com."
    if (Read-YesNo "Veux-tu que j'ouvre cette page maintenant ?") {
        Start-Process "https://console.anthropic.com/settings/keys"
    }
    $key = Read-Host "Colle ta clé Anthropic ici (ou laisse vide pour configurer plus tard)"
    if (-not [string]::IsNullOrWhiteSpace($key)) {
        Set-EnvValue "ANTHROPIC_API_KEY" $key.Trim()
        Write-Ok "Clé enregistrée dans .env (jamais partagée sur GitHub)."
    } else {
        Write-Host "OK, tu pourras l'ajouter plus tard en éditant le fichier .env." -ForegroundColor Yellow
    }
} else {
    Write-Ok "Une clé Anthropic est déjà configurée."
}

# --- 4. Microsoft / Planner ------------------------------------------------

Write-Title "Étape 4/6 — Microsoft Planner (optionnel)"

$existingClientId = Get-EnvValue "AZURE_CLIENT_ID"
$hasAzureId = $existingClientId -and $existingClientId -ne "00000000-0000-0000-0000-000000000000"

if (-not $hasAzureId) {
    Write-Info "Cette étape sert seulement à créer automatiquement des tâches"
    Write-Info "Planner à partir des réunions. Ce n'est PAS nécessaire pour"
    Write-Info "enregistrer et résumer des réunions — tu peux la passer."
    Write-Info ""
    Write-Info "Si quelqu'un de ton équipe utilise déjà TeamScribe, demande-lui"
    Write-Info "ses valeurs AZURE_CLIENT_ID et AZURE_TENANT_ID (souvent les mêmes"
    Write-Info "pour toute l'équipe)."
    if (Read-YesNo "As-tu ces informations sous la main maintenant ?" $false) {
        $clientId = Read-Host "AZURE_CLIENT_ID"
        $tenantId = Read-Host "AZURE_TENANT_ID (laisse vide pour 'organizations')"
        if (-not [string]::IsNullOrWhiteSpace($clientId)) {
            Set-EnvValue "AZURE_CLIENT_ID" $clientId.Trim()
            Set-EnvValue "AZURE_TENANT_ID" $(if ([string]::IsNullOrWhiteSpace($tenantId)) { "organizations" } else { $tenantId.Trim() })
            $hasAzureId = $true
            Write-Ok "Identifiants Microsoft enregistrés."
        }
    } else {
        Write-Host "Pas de problème — tu pourras configurer Planner plus tard en" -ForegroundColor Yellow
        Write-Host "éditant .env puis en lançant : python -m teamscribe.cli setup" -ForegroundColor Yellow
    }
} else {
    Write-Ok "Identifiants Microsoft déjà configurés."
}

if ($hasAzureId -and (Read-YesNo "Veux-tu choisir maintenant le plan/bucket Planner cible ?")) {
    python -m teamscribe.cli setup
}

# --- 5. Raccourcis -----------------------------------------------------

Write-Title "Étape 5/6 — Raccourcis pratiques"

if (Read-YesNo "Veux-tu un raccourci TeamScribe sur ton Bureau ?") {
    python -c "from teamscribe import shortcuts; print(shortcuts.create_desktop_shortcut())"
    Write-Ok "Raccourci créé sur le Bureau."
}

if (Read-YesNo "Veux-tu que TeamScribe se lance automatiquement au démarrage de Windows ?" $false) {
    python -c "from teamscribe import shortcuts; shortcuts.set_startup_enabled(True)"
    Write-Ok "Démarrage automatique activé (modifiable plus tard dans les Paramètres du widget)."
}

# --- 6. Lancement --------------------------------------------------------

Write-Title "Étape 6/6 — C'est prêt !"

Write-Host ""
Write-Host "Installation terminée. Voici comment utiliser TeamScribe :" -ForegroundColor White
Write-Host "  - Double-clique le raccourci sur ton Bureau (si créé) pour ouvrir" -ForegroundColor White
Write-Host "    le petit widget flottant." -ForegroundColor White
Write-Host "  - Le bouton vert démarre l'enregistrement, il devient rouge pendant" -ForegroundColor White
Write-Host "    que ça enregistre." -ForegroundColor White
Write-Host "  - Clique l'icône engrenage (haut-gauche) pour les options." -ForegroundColor White

if (Read-YesNo "Veux-tu ouvrir le widget TeamScribe maintenant ?") {
    $pythonw = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($pythonw) {
        Start-Process -FilePath $pythonw.Source -ArgumentList "-m", "teamscribe.cli", "gui"
    } else {
        Start-Process -FilePath "python" -ArgumentList "-m", "teamscribe.cli", "gui"
    }
    Write-Ok "Widget lancé !"
}

Write-Host ""
Write-Host "Bonne réunion !" -ForegroundColor Cyan
Read-Host "Appuie sur Entrée pour fermer"
