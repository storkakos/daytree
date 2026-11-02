"""UI translations. English is the source language: every string is its own key."""
from flask import request

from daytree.core import LANGUAGES, settings

GERMAN = {
    # Homepage
    "Today": "Heute",
    "Next 7 days": "Nächste 7 Tage",
    "Tasks": "Aufgaben",
    "New task": "Neue Aufgabe",
    "Add": "Hinzufügen",
    "All day": "Ganztägig",
    "Nothing planned today": "Heute steht nichts an",
    "No tasks": "Keine Aufgaben",
    "Delete task": "Aufgabe löschen",
    "Starts at {time} · tap to dismiss": "Beginnt um {time} · zum Schließen tippen",
    # First-time setup
    "Step 1 of 2": "Schritt 1 von 2",
    "Step 2 of 2": "Schritt 2 von 2",
    "Sign in to TimeTree": "Bei TimeTree anmelden",
    "Your account needs an e-mail address and password. Credentials are stored encrypted.":
        "Dein Konto braucht eine E-Mail-Adresse und ein Passwort. Die Zugangsdaten werden verschlüsselt gespeichert.",
    "E-mail": "E-Mail",
    "Password": "Passwort",
    "Sign in & load calendars": "Anmelden und Kalender laden",
    "Choose your calendar": "Kalender auswählen",
    "This calendar appears on the homepage and in the ICS feed.":
        "Dieser Kalender erscheint auf der Startseite und im ICS-Feed.",
    "Calendar": "Kalender",
    "Finish setup": "Einrichtung abschließen",
    # Settings: navigation and status
    "Home": "Startseite",
    "Settings": "Einstellungen",
    "Status": "Status",
    "Last sync succeeded": "Letzter Sync erfolgreich",
    "Last sync failed": "Letzter Sync fehlgeschlagen",
    "No sync yet": "Noch kein Sync",
    "Sync now": "Jetzt synchronisieren",
    "Running …": "Läuft …",
    "Last success": "Letzter Erfolg",
    "Events": "Termine",
    "Next sync": "Nächster Sync",
    "Every {interval}": "Alle {interval}",
    # Settings: account and calendar
    "TimeTree account": "TimeTree-Konto",
    "Leave empty to keep it": "Leer lassen, um es zu behalten",
    "A TimeTree account with an e-mail address and password is required. Logins through Google or Apple only have no password.":
        "Du brauchst ein TimeTree-Konto mit E-Mail-Adresse und Passwort. Reine Anmeldungen über Google oder Apple haben kein Passwort.",
    "Calendar and sync": "Kalender und Sync",
    "Sign in first, then the calendar list appears here.": "Melde dich zuerst an, dann erscheint hier die Kalenderliste.",
    "Sync interval": "Sync-Intervall",
    "Sync immediately afterwards": "Danach sofort synchronisieren",
    "Very short intervals may get you rate-limited by TimeTree.": "Sehr kurze Intervalle können zu Sperren durch TimeTree führen.",
    "Save": "Sichern",
    # Settings: language and time
    "Display": "Anzeige",
    "Show tasks on the homepage": "Aufgaben auf der Startseite anzeigen",
    "Language": "Sprache",
    "Automatic (browser)": "Automatisch (Browser)",
    "Time zone": "Zeitzone",
    "Default ({zone})": "Standard ({zone})",
    "Clock": "Uhr",
    "24 hours": "24 Stunden",
    "12 hours": "12 Stunden",
    "Date": "Datum",
    "Long": "Ausführlich",
    "Numeric": "Numerisch",
    "Time and date": "Zeit und Datum",
    "Show seconds": "Sekunden anzeigen",
    "Clock style": "Uhrart",
    "Digital": "Digital",
    "Analog": "Analog",
    "Clock size": "Uhrgröße",
    "Greeting": "Gruß",
    "Show greeting": "Gruß anzeigen",
    "Your name": "Dein Name",
    "Weather": "Wetter",
    "Show weather": "Wetter anzeigen",
    "Place": "Ort",
    "Unit": "Einheit",
    "Forecast": "Vorhersage",
    "Weather forecast": "Wettervorhersage",
    "Off": "Aus",
    "10 h": "10 Std.",
    "7 days": "7 Tage",
    "Place not found.": "Ort nicht gefunden.",
    "Background": "Hintergrund",
    "Type": "Typ",
    "Photos": "Fotos",
    "Color": "Farbe",
    "Change": "Wechsel",
    "Every hour": "Jede Stunde",
    "Every day": "Jeden Tag",
    "Never": "Nie",
    "Custom": "Eigene",
    "Minutes": "Minuten",
    "Blur": "Weichzeichner",
    "Brightness": "Helligkeit",
    "Apply": "Übernehmen",
    "Photos come from Lorem Picsum.": "Die Fotos kommen von Lorem Picsum.",
    "Waste collection": "Müllabfuhr",
    "Show waste collection": "Müllabfuhr anzeigen",
    "Calendar file (.ics)": "Kalenderdatei (.ics)",
    "Remove calendar": "Kalender entfernen",
    "Show bins one day earlier": "Tonnen einen Tag vorher anzeigen",
    "Each collection in the file gets its own color. The homepage shows a small bin next to the day.":
        "Jede Abfuhr aus der Datei bekommt eine eigene Farbe. Die Startseite zeigt neben dem Tag eine kleine Tonne.",
    "The file is too large.": "Die Datei ist zu groß.",
    "The file is not a usable calendar.": "Die Datei ist kein brauchbarer Kalender.",
    "Font": "Schrift",
    "Font family": "Schriftart",
    "System": "System",
    "Font size": "Schriftgröße",
    "Font weight": "Schriftstärke",
    "Other fonts are loaded from Google Fonts.": "Andere Schriften werden von Google Fonts geladen.",
    "Homepage": "Startseite",
    "Good morning": "Guten Morgen",
    "Good afternoon": "Guten Tag",
    "Good evening": "Guten Abend",
    "Good night": "Gute Nacht",
    "Photo by {name}": "Foto von {name}",
    "Clear sky": "Klar",
    "Partly cloudy": "Teilweise bewölkt",
    "Overcast": "Bedeckt",
    "Fog": "Nebel",
    "Drizzle": "Nieselregen",
    "Rain": "Regen",
    "Snow": "Schnee",
    "Thunderstorm": "Gewitter",
    # Settings: subscription
    "Calendar subscription": "Kalender-Abo",
    "ICS URL (home network)": "ICS-URL (Heimnetz)",
    "ICS URL (public)": "ICS-URL (öffentlich)",
    "Copy": "Kopieren",
    "Add this URL to your calendar app. Outlook on the web, Microsoft 365 and Google Calendar need the public URL.":
        "Füge diese URL in deiner Kalender-App hinzu. Outlook im Web, Microsoft 365 und Google Kalender brauchen die öffentliche URL.",
    "Generate new token": "Neues Token erzeugen",
    "The current URL stops working. Generate a new token?": "Die aktuelle URL funktioniert dann nicht mehr. Neues Token erzeugen?",
    # Settings: tunnel
    "Cloudflare tunnel": "Cloudflare-Tunnel",
    "Tunnel enabled": "Tunnel aktiv",
    "Connecting": "Verbindet",
    "Connected": "Verbunden",
    "Disconnected": "Getrennt",
    "Error": "Fehler",
    "cloudflared is not present in the image": "cloudflared ist im Image nicht vorhanden",
    "Tunnel token": "Tunnel-Token",
    "Token is stored": "Token ist gespeichert",
    "Delete the stored token": "Gespeichertes Token löschen",
    "Reachable through the tunnel": "Über den Tunnel erreichbar",
    "ICS feed": "ICS-Feed",
    "Calendar apps fetch the feed with its token.": "Kalender-Apps holen den Feed mit seinem Token.",
    "Homepage (read-only)": "Startseite (nur lesen)",
    "The homepage and /kiosk, without tasks and settings.": "Die Startseite und /kiosk, ohne Aufgaben und Einstellungen.",
    "API": "API",
    "Apps with an API token. Read-only tokens can only read.": "Apps mit einem API-Token. Tokens mit Lesezugriff können nur lesen.",
    "Tick what the tunnel may reach. With nothing ticked, only the /ping health check is reachable. Settings are never reachable.":
        "Hake an, was der Tunnel erreichen darf. Ist nichts angehakt, ist nur der Health-Check /ping erreichbar. Die Einstellungen sind nie erreichbar.",
    "Public hostname": "Öffentlicher Hostname",
    "The homepage has no login of its own. If you expose it, protect the hostname with a Cloudflare Access policy.":
        "Die Startseite hat keinen eigenen Login. Schütze den Hostnamen mit einer Cloudflare-Access-Richtlinie, wenn du sie freigibst.",
    "In Cloudflare, set the public hostname to the service HTTP and localhost:{port}.":
        "Stelle in Cloudflare beim öffentlichen Hostnamen den Dienst HTTP und localhost:{port} ein.",
    # Settings: about
    "Project website": "Projektseite",
    "Report a problem": "Problem melden",
    "Releases": "Versionen",
    "Made with": "Entwickelt mit",
    "by": "von",
    "Design inspired by": "Design inspiriert von",
    "Support Daytree": "Daytree unterstützen",
    # Settings: padlock
    "Padlock": "Schloss",
    "On": "An",
    "New password": "Neues Passwort",
    "Repeat password": "Passwort wiederholen",
    "Set password": "Passwort festlegen",
    "Change password": "Passwort ändern",
    "Lock now": "Jetzt sperren",
    "SETTINGS_LOCK in docker-compose.yml turns the padlock on. It closes by itself after 30 minutes without use.":
        "SETTINGS_LOCK in der docker-compose.yml schaltet das Schloss ein. Es schließt sich nach 30 Minuten ohne Nutzung von selbst.",
    "Settings are locked": "Einstellungen sind gesperrt",
    "Enter the password to open them.": "Gib das Passwort ein, um sie zu öffnen.",
    "Set a password": "Passwort festlegen",
    "Until a password is set, anyone on your home network can set one. The settings need it from then on.":
        "Solange kein Passwort festgelegt ist, kann es jeder in deinem Heimnetz festlegen. Danach brauchen die Einstellungen es.",
    "Unlock": "Entsperren",
    # Settings: API tokens
    "API tokens": "API-Tokens",
    "New token “{name}”": "Neues Token „{name}“",
    "Copy the token now. It is not shown again.": "Kopiere das Token jetzt. Es wird nicht noch einmal angezeigt.",
    "Read only": "Nur lesen",
    "Read and write": "Lesen und schreiben",
    "Created {date}": "Erstellt {date}",
    "Last used {date}": "Zuletzt genutzt {date}",
    "Never used": "Nie genutzt",
    "Revoke this token? Apps using it lose access.": "Dieses Token widerrufen? Apps, die es nutzen, verlieren den Zugriff.",
    "Revoke": "Widerrufen",
    "Name": "Name",
    "Access": "Zugriff",
    "Create token": "Token erstellen",
    "Apps send the token in the header Authorization: Bearer <token>. On the home network, reading needs no token, and writing from outside the homepage does.":
        "Apps senden das Token im Header Authorization: Bearer <token>. Im Heimnetz braucht Lesen kein Token, Schreiben außerhalb der Startseite schon.",
    # Messages
    "Enter e-mail and password.": "Gib E-Mail und Passwort ein.",
    "Login failed: {error}": "Anmeldung fehlgeschlagen: {error}",
    "Signed in. {count} calendars found.": "Angemeldet. {count} Kalender gefunden.",
    "New token generated. Update the ICS URL in your calendar apps.": "Neues Token erzeugt. Aktualisiere die ICS-URL in deinen Kalender-Apps.",
    "Settings saved.": "Einstellungen gesichert.",
    "Tunnel token not recognized (it normally starts with “eyJ”).": "Tunnel-Token nicht erkannt (es beginnt normalerweise mit „eyJ“).",
    "Invalid hostname.": "Ungültiger Hostname.",
    "Enter the tunnel token first to enable the tunnel.": "Gib zuerst das Tunnel-Token ein, um den Tunnel zu aktivieren.",
    "Sharing settings saved.": "Freigabe-Einstellungen gesichert.",
    "Wrong password.": "Falsches Passwort.",
    "Too many wrong passwords. Try again in a minute.": "Zu viele falsche Passwörter. Versuche es in einer Minute erneut.",
    "Password saved.": "Passwort gesichert.",
    "The password needs at least {count} characters.": "Das Passwort braucht mindestens {count} Zeichen.",
    "The passwords do not match.": "Die Passwörter stimmen nicht überein.",
    "Enter a name for the token.": "Gib einen Namen für das Token ein.",
    "Too many tokens. Revoke one first.": "Zu viele Tokens. Widerrufe zuerst eines.",
    "Token revoked.": "Token widerrufen.",
    "Please configure credentials and calendar under Settings": "Bitte Zugangsdaten und Kalender in den Einstellungen eintragen",
    "Timed out after 10 minutes": "Abbruch nach 10 Minuten",
    "unknown error": "Unbekannter Fehler",
}


def translate(text, language):
    return GERMAN.get(text, text) if language == "de" else text


def language():
    if settings["language"] in LANGUAGES:
        return settings["language"]
    if request.cookies.get("lang") in LANGUAGES:     # set by the homepage from the browser language
        return request.cookies["lang"]
    return request.accept_languages.best_match(LANGUAGES) or LANGUAGES[0]


def _(text):
    return translate(text, language())


def hours(minutes):
    value = f"{minutes / 60:g}"
    return f"{value.replace('.', ',')} Std." if language() == "de" else f"{value} h"
