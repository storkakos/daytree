"""Stale data on the homepage and notifications when syncs fail or recover."""
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from conftest import ORIGIN

from daytree import core, display, notify, sync

NTFY = "https://ntfy.example.com/daytree-secret-topic"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    core.settings.update(sources=[core.new_source("timetree", "Family", "fam123")], interval_minutes=180, language="en")
    saved = dict(sync.state)
    yield
    sync.state.clear()
    sync.state.update(saved)


def synced(minutes_ago, ok=True):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    sync.state.update(ok=ok, last_success=when.isoformat(timespec="seconds"))


@pytest.mark.parametrize("minutes, ok, note", [
    (10, True, ""), (6 * 60 + 29, True, ""),                       # within two intervals plus half an hour
    (6 * 60 + 31, True, "Last updated 6 h ago"),
    (20, False, "Last updated 20 min ago"),                        # a failed sync shows at once
    (3 * 24 * 60, False, "Last updated 3 days ago"),
])
def test_freshness(client, minutes, ok, note):
    synced(minutes, ok)
    assert client.get("/api/events", headers={"Accept-Language": "en"}).get_json()["stale"] == note


def test_freshness_before_the_first_success(client):
    sync.state.update(ok=None, last_success=None)
    assert client.get("/api/events").get_json()["stale"] == ""
    sync.state["ok"] = False
    assert client.get("/api/events", headers={"Accept-Language": "en"}).get_json()["stale"] == "Calendar not updated yet"
    core.settings["sources"] = []                            # not set up: the setup popup explains enough
    assert client.get("/api/events").get_json()["stale"] == ""


def test_homepage_has_the_note_and_its_offline_text(client):
    core.settings["language"] = "auto"
    page = client.get("/", headers={"Accept-Language": "de"}).data.decode()
    assert 'id="stale"' in page and "Keine Verbindung zu Daytree" in page


class Sent(list):
    def __call__(self, request, timeout):
        self.append(request)
        return mock.MagicMock()


@pytest.fixture
def sent(monkeypatch):
    calls = Sent()
    monkeypatch.setattr(notify.urllib.request, "urlopen", calls)
    core.settings.update(notify_url=NTFY, notify_format="ntfy")
    sync.state["alerted"] = False
    return calls


def run(ok):
    sync.state["ok"] = ok
    notify.after_sync(sync.state)


def test_one_message_per_failure_and_one_on_recovery(sent):
    run(False)
    run(False)
    assert len(sent) == 1 and sent[0].full_url == NTFY and sent[0].get_header("Title") == "Daytree: sync failed"
    assert b"could not be synced" in sent[0].data
    run(True)
    run(True)
    assert len(sent) == 2 and sent[1].get_header("Title") == "Daytree: sync works again"
    run(False)
    assert len(sent) == 3


def test_failed_delivery_is_tried_again(sent, monkeypatch):
    monkeypatch.setattr(notify.urllib.request, "urlopen", mock.MagicMock(side_effect=OSError("offline")))
    run(False)
    assert sync.state["alerted"] is False
    monkeypatch.setattr(notify.urllib.request, "urlopen", sent)
    run(False)
    assert len(sent) == 1 and sync.state["alerted"] is True


def test_json_webhook_and_language(sent):
    core.settings.update(notify_format="json", language="de")
    run(False)
    assert sent[0].get_header("Content-type") == "application/json"
    assert json.loads(sent[0].data) == {"title": "Daytree: Sync fehlgeschlagen",
                                        "message": "TimeTree konnte nicht synchronisiert werden. Die Startseite zeigt weiter die letzten Termine."}


def test_nothing_without_address_or_setup(sent):
    core.settings["notify_url"] = ""
    run(False)
    core.settings.update(notify_url=NTFY, sources=[])
    run(False)
    assert sent == [] and sync.state["alerted"] is False


def test_the_error_itself_is_not_sent(sent):
    sync.state["message"] = "Login failed for a@b.de"
    run(False)
    assert b"a@b.de" not in sent[0].data


def test_settings_store_the_address_encrypted(client, sent):
    core.settings["notify_url"] = ""
    r = client.post("/settings/notify", data={"notify_url": NTFY, "notify_format": "json"}, headers=ORIGIN)
    assert r.status_code == 302 and core.settings["notify_url"] == NTFY and core.settings["notify_format"] == "json"
    assert NTFY.encode() not in core.SETTINGS_FILE.read_bytes()
    page = client.get("/settings", headers={"Accept-Language": "en"}).data
    assert NTFY.encode() not in page and b"Address is stored" in page and b"Send test message" in page
    r = client.post("/settings/notify", data={"test": "1", "notify_format": "ntfy"}, headers=ORIGIN, follow_redirects=True)
    assert b"Test message sent." in r.data and len(sent) == 1 and core.settings["notify_url"] == NTFY
    client.post("/settings/notify", data={"clear_notify": "1"}, headers=ORIGIN)
    assert core.settings["notify_url"] == ""


@pytest.mark.parametrize("url", ["ftp://x", "ntfy.sh/topic", "https://a b", "https://" + "x" * 500])
def test_settings_reject_bad_addresses(client, url):
    r = client.post("/settings/notify", data={"notify_url": url}, headers=ORIGIN, follow_redirects=True)
    assert b"must start with" in r.data and core.settings["notify_url"] == ""


def test_run_sync_notifies(sent, monkeypatch, tmp_path):
    """The whole path: a failing exporter run sends one message."""
    monkeypatch.setattr(sync.subprocess, "run", mock.MagicMock(return_value=mock.MagicMock(returncode=1, stderr="boom", stdout="")))
    core.settings.update(email="a@b.de", password="x")
    sync.run_sync()
    assert sync.state["ok"] is False and len(sent) == 1
