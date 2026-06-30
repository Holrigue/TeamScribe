# TeamScribe — guided installer for Windows.
#
# Designed for a first-time user who has never touched the project: every
# step explains what's about to happen and asks for confirmation first.
# No admin elevation is required (everything stays in user mode).
#
# Usage: right-click this file -> "Run with PowerShell"
# (or, from a PowerShell terminal opened in this folder: .\install.ps1)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# ---------------------------------------------------------------------------
# i18n — English by default, French available. Add a language, add a row.
# ---------------------------------------------------------------------------

$T = @{
    welcome1        = @{en = "Welcome! This script will install TeamScribe on your computer,"; fr = "Bienvenue ! Ce script va installer TeamScribe sur ton ordinateur,"}
    welcome2        = @{en = "step by step. You can answer 'y' (yes) or 'n' (no) to each"; fr = "étape par étape. Tu peux répondre 'o' (oui) ou 'n' (non) à chaque"}
    welcome3        = @{en = "question - just press Enter to accept the default choice"; fr = "question — appuie simplement sur Entrée pour accepter le choix par"}
    welcome4        = @{en = "(shown in uppercase)."; fr = "défaut (en majuscule)."}
    step1_title     = @{en = "Step 1/6 - Checking Python"; fr = "Étape 1/6 — Vérification de Python"}
    python_found    = @{en = "Python detected: {0}"; fr = "Python détecté : {0}"}
    python_missing1 = @{en = "Python 3.10 or newer was not detected on this computer."; fr = "Python 3.10 ou plus récent n'a pas été détecté sur cet ordinateur."}
    python_missing2 = @{en = "TeamScribe needs it to run."; fr = "TeamScribe en a besoin pour fonctionner."}
    open_python_dl  = @{en = "Do you want me to open the Python download page?"; fr = "Veux-tu que j'ouvre la page de téléchargement de Python ?"}
    python_instr1   = @{en = "Install Python (make sure to check 'Add python.exe to PATH' during"; fr = "Installe Python (coche bien 'Add python.exe to PATH' pendant"}
    python_instr2   = @{en = "setup), then re-run this script (install.ps1)."; fr = "l'installation), puis relance ce script (install.ps1)."}
    press_enter_close= @{en = "Press Enter to close"; fr = "Appuie sur Entrée pour fermer"}
    step2_title     = @{en = "Step 2/6 - Installing TeamScribe components"; fr = "Étape 2/6 — Installation des composants TeamScribe"}
    step2_info1     = @{en = "This downloads and installs the needed libraries (audio capture,"; fr = "Ceci télécharge et installe les librairies nécessaires (capture audio,"}
    step2_info2     = @{en = "transcription, interface) into your user account - no admin rights"; fr = "transcription, interface) dans ton compte utilisateur — pas besoin"}
    step2_info3     = @{en = "needed. This can take a few minutes."; fr = "de droits administrateur. Ça peut prendre quelques minutes."}
    has_gpu         = @{en = "Do you have a dedicated NVIDIA graphics card (for faster transcription)?"; fr = "As-tu une carte graphique NVIDIA dédiée (pour une transcription plus rapide) ?"}
    installing      = @{en = "Installing..."; fr = "Installation en cours…"}
    install_failed1 = @{en = "Installation failed - copy the error message above and"; fr = "L'installation a échoué — copie le message d'erreur ci-dessus et"}
    install_failed2 = @{en = "send it to whoever shared this project with you."; fr = "envoie-le à la personne qui t'a partagé ce projet."}
    components_ok   = @{en = "Components installed."; fr = "Composants installés."}
    step3_title     = @{en = "Step 3/6 - AI provider (for automatic summaries)"; fr = "Étape 3/6 — Fournisseur IA (pour les résumés automatiques)"}
    ai_choose       = @{en = "TeamScribe can use 4 different AI providers to summarize your meetings."; fr = "TeamScribe peut utiliser 4 fournisseurs IA différents pour résumer tes réunions."}
    ai_choose2      = @{en = "Each user needs their OWN API key - keys are never shared."; fr = "Chaque utilisateur a besoin de SA PROPRE clé API — les clés ne sont jamais partagées."}
    ai_pick         = @{en = "Which AI provider do you want to use?"; fr = "Quel fournisseur IA veux-tu utiliser ?"}
    ai_opt1         = @{en = "  [1] Claude (Anthropic) - recommended, default"; fr = "  [1] Claude (Anthropic) — recommandé, par défaut"}
    ai_opt2         = @{en = "  [2] ChatGPT (OpenAI)"; fr = "  [2] ChatGPT (OpenAI)"}
    ai_opt3         = @{en = "  [3] Copilot (Azure OpenAI)"; fr = "  [3] Copilot (Azure OpenAI)"}
    ai_opt4         = @{en = "  [4] Gemini (Google)"; fr = "  [4] Gemini (Google)"}
    ai_get_key      = @{en = "Get your key at: {0}"; fr = "Obtiens ta clé sur : {0}"}
    open_page_now   = @{en = "Do you want me to open this page now?"; fr = "Veux-tu que j'ouvre cette page maintenant ?"}
    paste_key       = @{en = "Paste your API key here (or leave empty to configure later)"; fr = "Colle ta clé API ici (ou laisse vide pour configurer plus tard)"}
    azure_endpoint  = @{en = "Paste your Azure OpenAI endpoint (e.g. https://my-resource.openai.azure.com/)"; fr = "Colle ton endpoint Azure OpenAI (ex. https://my-resource.openai.azure.com/)"}
    azure_deploy    = @{en = "Deployment name (leave empty for default: gpt-4o)"; fr = "Nom du déploiement (laisse vide pour le défaut : gpt-4o)"}
    key_saved       = @{en = "Key saved to .env (never shared on GitHub)."; fr = "Clé enregistrée dans .env (jamais partagée sur GitHub)."}
    key_later       = @{en = "OK, you can add it later by editing the .env file."; fr = "OK, tu pourras l'ajouter plus tard en éditant le fichier .env."}
    key_already     = @{en = "An API key is already configured for this provider."; fr = "Une clé API est déjà configurée pour ce fournisseur."}
    step4_title     = @{en = "Step 4/6 - Microsoft Planner (optional)"; fr = "Étape 4/6 — Microsoft Planner (optionnel)"}
    planner_info1   = @{en = "This step is only used to automatically create Planner tasks"; fr = "Cette étape sert seulement à créer automatiquement des tâches"}
    planner_info2   = @{en = "from your meetings. It is NOT required to record and"; fr = "Planner à partir des réunions. Ce n'est PAS nécessaire pour"}
    planner_info3   = @{en = "summarize meetings - you can skip it."; fr = "enregistrer et résumer des réunions — tu peux la passer."}
    planner_info4   = @{en = "If someone on your team already uses TeamScribe, ask them for"; fr = "Si quelqu'un de ton équipe utilise déjà TeamScribe, demande-lui"}
    planner_info5   = @{en = "their AZURE_CLIENT_ID and AZURE_TENANT_ID (often the same for"; fr = "ses valeurs AZURE_CLIENT_ID et AZURE_TENANT_ID (souvent les mêmes"}
    planner_info6   = @{en = "the whole team)."; fr = "pour toute l'équipe)."}
    have_ids_now    = @{en = "Do you have this information on hand right now?"; fr = "As-tu ces informations sous la main maintenant ?"}
    ms_saved        = @{en = "Microsoft identifiers saved."; fr = "Identifiants Microsoft enregistrés."}
    planner_later1  = @{en = "No problem - you can set up Planner later by editing .env"; fr = "Pas de problème — tu pourras configurer Planner plus tard en"}
    planner_later2  = @{en = "then running: python -m teamscribe.cli setup"; fr = "éditant .env puis en lançant : python -m teamscribe.cli setup"}
    ms_already      = @{en = "Microsoft identifiers already configured."; fr = "Identifiants Microsoft déjà configurés."}
    choose_plan_now = @{en = "Do you want to choose the target Planner plan/bucket now?"; fr = "Veux-tu choisir maintenant le plan/bucket Planner cible ?"}
    step5_title     = @{en = "Step 5/6 - Handy shortcuts"; fr = "Étape 5/6 — Raccourcis pratiques"}
    want_desktop    = @{en = "Do you want a TeamScribe shortcut on your Desktop?"; fr = "Veux-tu un raccourci TeamScribe sur ton Bureau ?"}
    shortcut_ok     = @{en = "Shortcut created on the Desktop."; fr = "Raccourci créé sur le Bureau."}
    want_startup    = @{en = "Do you want TeamScribe to launch automatically at Windows startup?"; fr = "Veux-tu que TeamScribe se lance automatiquement au démarrage de Windows ?"}
    startup_ok      = @{en = "Auto-start enabled (changeable later in the widget's Settings)."; fr = "Démarrage automatique activé (modifiable plus tard dans les Paramètres du widget)."}
    step6_title     = @{en = "Step 6/6 - All set!"; fr = "Étape 6/6 — C'est prêt !"}
    done1           = @{en = "Installation complete. Here's how to use TeamScribe:"; fr = "Installation terminée. Voici comment utiliser TeamScribe :"}
    done2           = @{en = "  - Double-click the Desktop shortcut (if created) to open"; fr = "  - Double-clique le raccourci sur ton Bureau (si créé) pour ouvrir"}
    done3           = @{en = "    the small floating widget."; fr = "    le petit widget flottant."}
    done4           = @{en = "  - The green button starts recording; it turns red while"; fr = "  - Le bouton vert démarre l'enregistrement, il devient rouge pendant"}
    done5           = @{en = "    it's recording."; fr = "    que ça enregistre."}
    done6           = @{en = "  - Click the gear icon (top-left) for options."; fr = "  - Clique l'icône engrenage (haut-gauche) pour les options."}
    open_widget_now = @{en = "Do you want to open the TeamScribe widget now?"; fr = "Veux-tu ouvrir le widget TeamScribe maintenant ?"}
    widget_launched = @{en = "Widget launched!"; fr = "Widget lancé !"}
    bye             = @{en = "Have a great meeting!"; fr = "Bonne réunion !"}
}

$Lang = "en"
function L($key) { $T[$key][$Lang] }

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
    $hint = if ($defaultYes) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "$question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $defaultYes }
    return $answer.Trim().ToLower().StartsWith("y") -or $answer.Trim().ToLower().StartsWith("o")
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
# Language choice (first thing, before anything else is printed)
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Choose your language / Choisis ta langue :" -ForegroundColor White
Write-Host "  [1] English (default)"
Write-Host "  [2] Francais"
$langChoice = Read-Host "[1/2]"
if ($langChoice.Trim() -eq "2") { $Lang = "fr" }

Write-Host ""
Write-Host (L welcome1) -ForegroundColor White
Write-Host (L welcome2) -ForegroundColor White
Write-Host (L welcome3) -ForegroundColor White
Write-Host (L welcome4) -ForegroundColor White

# --- 1. Python ---------------------------------------------------------

Write-Title (L step1_title)

$python = Get-Command python -ErrorAction SilentlyContinue
$pythonOk = $false
if ($python) {
    $versionText = (& python --version) 2>&1
    if ($versionText -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
            $pythonOk = $true
            Write-Ok ((L python_found) -f $versionText)
        }
    }
}

if (-not $pythonOk) {
    Write-Host ""
    Write-Host (L python_missing1) -ForegroundColor Yellow
    Write-Host (L python_missing2) -ForegroundColor Yellow
    if (Read-YesNo (L open_python_dl)) {
        Start-Process "https://www.python.org/downloads/"
    }
    Write-Host ""
    Write-Host (L python_instr1) -ForegroundColor Yellow
    Write-Host (L python_instr2) -ForegroundColor Yellow
    Read-Host (L press_enter_close)
    exit 1
}

# --- 2. Dépendances ------------------------------------------------------

Write-Title (L step2_title)
Write-Info (L step2_info1)
Write-Info (L step2_info2)
Write-Info (L step2_info3)

$hasGpu = Read-YesNo (L has_gpu) $false

Write-Host ""
Write-Info (L installing)
if ($hasGpu) {
    python -m pip install --user -e ".[gpu]"
} else {
    python -m pip install --user -e .
}
if ($LASTEXITCODE -ne 0) {
    Write-Host (L install_failed1) -ForegroundColor Red
    Write-Host (L install_failed2) -ForegroundColor Red
    Read-Host (L press_enter_close)
    exit 1
}
Write-Ok (L components_ok)
python -c "from teamscribe import config; s = config.load_gui_settings(); s['language'] = '$Lang'; config.save_gui_settings(s)"

# --- 3. Fournisseur IA (résumés) -----------------------------------------

Write-Title (L step3_title)

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

Write-Info (L ai_choose)
Write-Info (L ai_choose2)
Write-Host ""
Write-Host (L ai_pick) -ForegroundColor White
Write-Host (L ai_opt1)
Write-Host (L ai_opt2)
Write-Host (L ai_opt3)
Write-Host (L ai_opt4)
$providerChoice = Read-Host "[1/2/3/4]"

$providerCode = "anthropic"
$providerKeyEnv = "ANTHROPIC_API_KEY"
$providerKeyUrl = "https://console.anthropic.com/settings/keys"
$providerKeyPlaceholder = "sk-ant-xxx*"

switch ($providerChoice.Trim()) {
    "2" {
        $providerCode = "openai"
        $providerKeyEnv = "OPENAI_API_KEY"
        $providerKeyUrl = "https://platform.openai.com/api-keys"
        $providerKeyPlaceholder = "sk-xxx*"
    }
    "3" {
        $providerCode = "azure_openai"
        $providerKeyEnv = "AZURE_OPENAI_API_KEY"
        $providerKeyUrl = "https://portal.azure.com/"
        $providerKeyPlaceholder = ""
    }
    "4" {
        $providerCode = "gemini"
        $providerKeyEnv = "GEMINI_API_KEY"
        $providerKeyUrl = "https://aistudio.google.com/app/apikey"
        $providerKeyPlaceholder = ""
    }
}

Set-EnvValue "TEAMSCRIBE_LLM_PROVIDER" $providerCode

$existingKey = Get-EnvValue $providerKeyEnv
$needsKey = -not $existingKey -or ($providerKeyPlaceholder -and $existingKey -like $providerKeyPlaceholder)

if ($needsKey) {
    Write-Info ((L ai_get_key) -f $providerKeyUrl)
    if (Read-YesNo (L open_page_now)) {
        Start-Process $providerKeyUrl
    }
    $key = Read-Host (L paste_key)
    if (-not [string]::IsNullOrWhiteSpace($key)) {
        Set-EnvValue $providerKeyEnv $key.Trim()
        if ($providerCode -eq "azure_openai") {
            $endpoint = Read-Host (L azure_endpoint)
            if (-not [string]::IsNullOrWhiteSpace($endpoint)) {
                Set-EnvValue "AZURE_OPENAI_ENDPOINT" $endpoint.Trim()
            }
            $deploy = Read-Host (L azure_deploy)
            if (-not [string]::IsNullOrWhiteSpace($deploy)) {
                Set-EnvValue "AZURE_OPENAI_DEPLOYMENT" $deploy.Trim()
            }
        }
        Write-Ok (L key_saved)
    } else {
        Write-Host (L key_later) -ForegroundColor Yellow
    }
} else {
    Write-Ok (L key_already)
}

# --- 4. Microsoft / Planner ------------------------------------------------

Write-Title (L step4_title)

$existingClientId = Get-EnvValue "AZURE_CLIENT_ID"
$hasAzureId = $existingClientId -and $existingClientId -ne "00000000-0000-0000-0000-000000000000"

if (-not $hasAzureId) {
    Write-Info (L planner_info1)
    Write-Info (L planner_info2)
    Write-Info (L planner_info3)
    Write-Info ""
    Write-Info (L planner_info4)
    Write-Info (L planner_info5)
    Write-Info (L planner_info6)
    if (Read-YesNo (L have_ids_now) $false) {
        $clientId = Read-Host "AZURE_CLIENT_ID"
        $tenantId = Read-Host "AZURE_TENANT_ID"
        if (-not [string]::IsNullOrWhiteSpace($clientId)) {
            Set-EnvValue "AZURE_CLIENT_ID" $clientId.Trim()
            Set-EnvValue "AZURE_TENANT_ID" $(if ([string]::IsNullOrWhiteSpace($tenantId)) { "organizations" } else { $tenantId.Trim() })
            $hasAzureId = $true
            Write-Ok (L ms_saved)
        }
    } else {
        Write-Host (L planner_later1) -ForegroundColor Yellow
        Write-Host (L planner_later2) -ForegroundColor Yellow
    }
} else {
    Write-Ok (L ms_already)
}

if ($hasAzureId -and (Read-YesNo (L choose_plan_now))) {
    python -m teamscribe.cli setup
}

# --- 5. Raccourcis -----------------------------------------------------

Write-Title (L step5_title)

if (Read-YesNo (L want_desktop)) {
    python -c "from teamscribe import shortcuts; print(shortcuts.create_desktop_shortcut())"
    Write-Ok (L shortcut_ok)
}

if (Read-YesNo (L want_startup) $false) {
    python -c "from teamscribe import shortcuts; shortcuts.set_startup_enabled(True)"
    Write-Ok (L startup_ok)
}

# --- 6. Lancement --------------------------------------------------------

Write-Title (L step6_title)

Write-Host ""
Write-Host (L done1) -ForegroundColor White
Write-Host (L done2) -ForegroundColor White
Write-Host (L done3) -ForegroundColor White
Write-Host (L done4) -ForegroundColor White
Write-Host (L done5) -ForegroundColor White
Write-Host (L done6) -ForegroundColor White

if (Read-YesNo (L open_widget_now)) {
    $pythonw = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($pythonw) {
        Start-Process -FilePath $pythonw.Source -ArgumentList "-m", "teamscribe.cli", "gui"
    } else {
        Start-Process -FilePath "python" -ArgumentList "-m", "teamscribe.cli", "gui"
    }
    Write-Ok (L widget_launched)
}

Write-Host ""
Write-Host (L bye) -ForegroundColor Cyan
Read-Host (L press_enter_close)
