"""Notifications when syncs start failing and when they work again, sent to ntfy or to a JSON webhook."""
import json
import urllib.request

from daytree.core import LANGUAGES, needs_setup, settings
from daytree.i18n import translate

FORMATS = ("ntfy", "json")


def _(text):
    """Messages leave without a browser, so they follow the language setting, or English for "auto"."""
    return translate(text, settings["language"] if settings["language"] in LANGUAGES else "en")


def send(title, message):
    """True if the service accepted the message. Fails quietly: a sync never waits more than a few seconds for it."""
    url = settings["notify_url"]
    if settings["notify_format"] == "json":
        body = json.dumps({"title": title, "message": message}).encode()
        headers = {"Content-Type": "application/json"}
    else:                                   # ntfy takes the text as body and the title from a header
        body = message.encode()
        headers = {"Content-Type": "text/plain; charset=utf-8", "Title": title.encode().decode("latin-1", "replace"),
                   "Tags": "calendar"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method="POST"), timeout=8):
            return True
    except Exception:  # noqa: BLE001
        return False


def after_sync(state):
    """One message when syncs start failing and one when they work again. The error itself is not sent, because it
    can contain account details; the settings show it."""
    if not settings["notify_url"] or needs_setup():
        state["alerted"] = False
        return
    if state["ok"] is False and not state.get("alerted"):
        if send(_("Daytree: sync failed"), _("TimeTree could not be synced. The homepage keeps showing the last events.")):
            state["alerted"] = True
    elif state["ok"] and state.get("alerted"):
        if send(_("Daytree: sync works again"), _("TimeTree is synced again.")):
            state["alerted"] = False


def test():
    return send(_("Daytree: test message"), _("Notifications from Daytree arrive here."))
