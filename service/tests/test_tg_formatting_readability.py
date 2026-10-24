import pytest
from app.tg.client import strip_html_tags, TelegramClient
from app.tg.formatters import _format_mode, _format_config, _format_agree, _format_ai
from app.models import HeartbeatRequest


def test_strip_html_tags():
    assert strip_html_tags("<b>bold text</b>") == "bold text"
    assert strip_html_tags("⏹️ <b>₿ BTCUSD HALTED & SECURED</b>") == "⏹️ ₿ BTCUSD HALTED & SECURED"
    assert strip_html_tags("• Safety: Auto-entries <b>PAUSED</b>") == "• Safety: Auto-entries PAUSED"
    assert strip_html_tags("Tap <b>[ ₿ BTC: ▶️ PLAY ]</b> to resume.") == "Tap [ ₿ BTC: ▶️ PLAY ] to resume."
    assert strip_html_tags("<code>iziBit5</code>") == "iziBit5"
    assert strip_html_tags("Status: <span class='ok'>OK</span>") == "Status: OK"
    # Math & comparisons must remain untouched
    assert strip_html_tags("age < 30") == "age < 30"
    assert strip_html_tags("0 < x < 10") == "0 < x < 10"
    assert strip_html_tags("Normal text without tags") == "Normal text without tags"


def test_telegram_client_strips_html_before_transport():
    sent_payloads = []

    def fake_transport(method, payload, files=None):
        sent_payloads.append((method, payload))
        return {"ok": True}

    client = TelegramClient("token", "12345", transport=fake_transport)
    client.send_message("<b>Hello</b> <i>World</i>!")
    assert sent_payloads[0][1]["text"] == "Hello World!"

    client.edit_message(99, "Safety: <b>ARMED</b>")
    assert sent_payloads[1][1]["text"] == "Safety: ARMED"

    client.send_photo("Caption with <code>code</code>", b"fake_png")
    assert sent_payloads[2][1]["caption"] == "Caption with code"


def test_format_mode_is_concise_under_2s():
    class FakeApp:
        class state:
            class db:
                @staticmethod
                def exec_mode():
                    return "auto"
                @staticmethod
                def entry_mode():
                    return "hybrid"
            latest_heartbeat = (1000.0, HeartbeatRequest(
                equity=4300.0, balance=4300.0, floating_pl=0.0,
                active_strategy="iziGold15", symbol="XAUUSD", positions=[]
            ))
            latest_heartbeats = {
                "XAUUSD": (1000.0, HeartbeatRequest(equity=4300.0, balance=4300.0, floating_pl=0.0, active_strategy="iziGold15", symbol="XAUUSD", positions=[])),
                "BTCUSD": (1000.0, HeartbeatRequest(equity=4300.0, balance=4300.0, floating_pl=0.0, active_strategy="iziBit5", symbol="BTCUSD", positions=[])),
            }
            pending_switch = None

    text, kb = _format_mode(FakeApp())
    lines = text.strip().split("\n")
    # Must be 5 lines or fewer, crisp and readable in < 2s
    assert len(lines) <= 6
    assert "AUTO" in text
    assert "HYBRID" in text
    assert "Gold" in text
    assert "Bitcoin" in text
    # No wordy essay paragraphs
    assert "executes signals immediately; MANUAL sends proposals" not in text
    assert "<" not in text and ">" not in text
