"""Minimal i18n for the desktop widget.

A flat dict of ``{key: {"en": ..., "fr": ...}}``. English is the default
for new installs; an existing user's saved language is never overridden.
Switching language in Settings requires a restart, since the widget's UI
is built once at launch — simpler and more robust than re-texting every
widget live.
"""

from __future__ import annotations

_STRINGS: dict[str, dict[str, str]] = {
    # Settings dialog
    "settings_title": {"en": "Settings — TeamScribe", "fr": "Paramètres — TeamScribe"},
    "launch_at_startup": {"en": "Launch at Windows startup", "fr": "Lancer au démarrage de Windows"},
    "auto_summarize": {"en": "Automatically summarize after recording", "fr": "Résumer automatiquement après l'enregistrement"},
    "always_on_top": {"en": "Always on top of other windows", "fr": "Toujours par-dessus les autres fenêtres"},
    "theme_label": {"en": "Theme:", "fr": "Thème :"},
    "theme_dark": {"en": "Dark", "fr": "Sombre"},
    "theme_light": {"en": "Light", "fr": "Clair"},
    "glass_opacity_label": {"en": "Liquid glass effect (transparency): {value}%", "fr": "Effet verre liquide (transparence) : {value}%"},
    "create_desktop_shortcut": {"en": "Create a desktop shortcut", "fr": "Créer un raccourci sur le bureau"},
    "check_audio": {"en": "Check audio", "fr": "Vérifier l'audio"},
    "update_label": {"en": "Update:", "fr": "Mise à jour :"},
    "check_now": {"en": "Check now", "fr": "Vérifier maintenant"},
    "update_now": {"en": "Update", "fr": "Mettre à jour"},
    "save": {"en": "Save", "fr": "Enregistrer"},
    "cancel": {"en": "Cancel", "fr": "Annuler"},
    "kofi_link": {
        "en": '<a href="https://ko-fi.com/gabrielhoule">☕ Support TeamScribe on Ko-fi</a>',
        "fr": '<a href="https://ko-fi.com/gabrielhoule">☕ Soutenir TeamScribe sur Ko-fi</a>',
    },
    "version_label": {"en": "Version {version}", "fr": "Version {version}"},
    "language_label": {"en": "Language:", "fr": "Langue :"},
    "language_restart_note": {
        "en": "Changing language takes effect after a restart.",
        "fr": "Le changement de langue prend effet après un redémarrage.",
    },

    # Update status / actions
    "update_check_unavailable": {
        "en": "Check unavailable (project not installed via git clone).",
        "fr": "Vérification indisponible (projet non cloné via git).",
    },
    "update_check_failed": {"en": "Check failed: {error}", "fr": "Vérification impossible : {error}"},
    "update_available": {"en": "An update is available ({behind} commit(s)).", "fr": "Une mise à jour est disponible ({behind} commit(s))."},
    "update_up_to_date": {"en": "TeamScribe is up to date.", "fr": "TeamScribe est à jour."},
    "update_checking": {"en": "Checking…", "fr": "Vérification…"},
    "update_confirm_title": {"en": "Update", "fr": "Mettre à jour"},
    "update_confirm_body": {
        "en": "Download and install the latest version of TeamScribe?\nThe app will need to be restarted afterwards.",
        "fr": "Télécharger et installer la dernière version de TeamScribe ?\nL'application devra être redémarrée après.",
    },
    "update_in_progress": {"en": "Updating…", "fr": "Mise à jour en cours…"},
    "update_failed": {"en": "Update failed:\n{error}", "fr": "Échec de la mise à jour :\n{error}"},
    "update_done": {"en": "Update complete. Restart required.", "fr": "Mise à jour terminée. Redémarrage nécessaire."},
    "restart_confirm_title": {"en": "Restart", "fr": "Redémarrer"},
    "restart_confirm_body": {
        "en": "Restart TeamScribe now to apply the update?",
        "fr": "Redémarrer TeamScribe maintenant pour appliquer la mise à jour ?",
    },
    "startup_toggle_failed": {
        "en": "Failed to update the startup launch setting:\n{error}",
        "fr": "Échec de la mise à jour du démarrage automatique :\n{error}",
    },
    "shortcut_create_failed": {"en": "Failed to create shortcut:\n{error}", "fr": "Échec de la création :\n{error}"},
    "shortcut_created": {"en": "Shortcut created:\n{path}", "fr": "Raccourci créé :\n{path}"},
    "audio_check_result": {
        "en": "Speakers: {speakers}\nMicrophone: {mic}\nLoopback devices detected: {count}",
        "fr": "Haut-parleurs : {speakers}\nMicro : {mic}\nPériphériques loopback détectés : {count}",
    },
    "audio_title": {"en": "Audio", "fr": "Audio"},

    # Widget chrome
    "pin_tooltip_off": {"en": "Pin (lock position)", "fr": "Épingler (bloquer le déplacement)"},
    "pin_tooltip_on": {"en": "Unpin (allow moving)", "fr": "Désépingler (autoriser le déplacement)"},
    "settings_tooltip": {"en": "Settings", "fr": "Paramètres"},
    "restart_tooltip": {"en": "Restart the app", "fr": "Redémarrer l'application"},
    "minimize_tooltip_expand": {"en": "Expand widget", "fr": "Agrandir le widget"},
    "minimize_tooltip_collapse": {"en": "Collapse to title bar", "fr": "Réduire à la barre de titre"},
    "status_ready": {"en": "Ready", "fr": "Prêt"},
    "record_start": {"en": "● Start recording", "fr": "● Démarrer l'enregistrement"},
    "mic_audio_kept": {"en": "🎙️ Audio kept", "fr": "🎙️ Audio conservé"},
    "mic_audio_not_kept": {"en": "🎙️🚫 Audio not kept (privacy mode)", "fr": "🎙️🚫 Audio non conservé (mode privé)"},
    "mic_tooltip_off": {
        "en": "Click to stop keeping a local audio copy (transcript only)",
        "fr": "Clique pour ne plus garder de copie audio locale (texte seulement)",
    },
    "mic_tooltip_on": {
        "en": "Click to keep the local audio copy again",
        "fr": "Clique pour garder à nouveau la copie audio locale",
    },
    "record_stop": {"en": "■ Stop recording", "fr": "■ Arrêter l'enregistrement"},
    "recent_sessions": {"en": "Recent sessions:", "fr": "Sessions récentes :"},
    "summarize_btn": {"en": "Summarize", "fr": "Résumer"},
    "push_planner_btn": {"en": "Push → Planner", "fr": "Pousser → Planner"},
    "refresh_list_btn": {"en": "Refresh list", "fr": "Rafraîchir la liste"},
    "open_folder_btn": {"en": "Open folder", "fr": "Ouvrir dossier"},

    # Recording status
    "stopping": {"en": "Stopping…", "fr": "Arrêt en cours…"},
    "phase_transcribing": {"en": "Transcribing audio…", "fr": "Transcription audio…"},
    "phase_summarizing": {"en": "Summarizing…", "fr": "Résumé en cours…"},
    "phase_saving": {"en": "Saving…", "fr": "Sauvegarde…"},
    "settings_loading": {"en": "Loading…", "fr": "Chargement…"},
    "listening": {"en": "Listening… {time}", "fr": "À l'écoute… {time}"},
    "record_done": {"en": "Done: {name}", "fr": "Terminé : {name}"},
    "error": {"en": "Error", "fr": "Erreur"},
    "record_failed": {"en": "Recording failed:\n{error}", "fr": "Échec de l'enregistrement :\n{error}"},
    "restart_blocked": {
        "en": "Stop the current recording before restarting.",
        "fr": "Arrête l'enregistrement en cours avant de redémarrer.",
    },

    # Session deletion
    "delete_session_title": {"en": "Delete session", "fr": "Supprimer la session"},
    "delete_session_body": {"en": "What do you want to do with {names}?", "fr": "Que faire avec {names} ?"},
    "remove_from_widget": {"en": "Remove from widget only", "fr": "Retirer du widget seulement"},
    "delete_folders": {"en": "Delete the folder(s) completely", "fr": "Supprimer le(s) dossier(s) complet(s)"},
    "confirm_delete_title": {"en": "Confirm deletion", "fr": "Confirmer la suppression"},
    "confirm_delete_body": {
        "en": "Permanently delete {names} and all their content (audio, transcript, summary)? "
              "This action is irreversible.",
        "fr": "Supprimer définitivement {names} et tout leur contenu (audio, transcription, "
              "résumé) ? Cette action est irréversible.",
    },
    "delete_failed": {"en": "Deletion failed:\n{errors}", "fr": "Échec de la suppression :\n{errors}"},
    "delete_action": {"en": "Delete…", "fr": "Supprimer…"},
    "names_single": {"en": '"{name}"', "fr": "« {name} »"},
    "names_plural": {"en": "these {count} sessions", "fr": "ces {count} sessions"},

    # Summarize / push tasks
    "select_session_first": {"en": "Select a session first.", "fr": "Sélectionne une session d'abord."},
    "summarizing": {"en": "Summarizing {name}…", "fr": "Résumé en cours pour {name}…"},
    "summary_done": {"en": "Summary done", "fr": "Résumé terminé"},
    "resummarized": {"en": "Re-summarized: {name}", "fr": "Résumé refait : {name}"},
    "no_summary_yet": {
        "en": "No summary.json — run 'Summarize' first.",
        "fr": "Pas de summary.json — fais 'Résumer' d'abord.",
    },
    "sending_to_planner": {"en": "Sending to Planner…", "fr": "Envoi vers Planner…"},
    "formatting_then_pushing": {"en": "Formatting notes then pushing to Planner…", "fr": "Mise en forme des notes puis envoi vers Planner…"},
    "tasks_pushed": {"en": "Tasks pushed", "fr": "Tâches poussées"},
    "no_actions_to_push": {"en": "No actions to push.", "fr": "Aucune action à pousser."},
    "planner_task_notes": {"en": "Created by TeamScribe ({name}).", "fr": "Créé par TeamScribe ({name})."},
    "planner_tasks_created": {"en": "{count} task(s) created in Planner.", "fr": "{count} tâche(s) créée(s) dans Planner."},
}


def tr(key: str, lang: str, **kwargs) -> str:
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get("en") or key
    return text.format(**kwargs) if kwargs else text
