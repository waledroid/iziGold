"""/manual: sendDocument transport encoding and the poller-level handler
that ships docs/izi_manual.pdf into the owner chat."""
import asyncio
import types

from app.telegram import TelegramClient, format_pinned_help


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return {"ok": True, "result": {"message_id": 7}}


def test_send_document_hits_senddocument_with_pdf_multipart():
    ft = FakeTransport()
    client = TelegramClient("tok", "555", transport=ft)
    client.send_document("the manual", b"%PDF-1.4 data", "izi_manual.pdf")
    method, payload, files = ft.calls[0]
    assert method == "sendDocument"
    assert payload["chat_id"] == "555"
    assert payload["caption"] == "the manual"
    assert files == {"document": ("izi_manual.pdf", b"%PDF-1.4 data",
                                  "application/pdf")}


class _RecTg:
    def __init__(self):
        self.docs = []
        self.texts = []

    def send_document(self, caption, pdf_bytes, filename):
        self.docs.append((caption, pdf_bytes, filename))
        return {"ok": True}

    def send_message(self, text, reply_markup=None):
        self.texts.append(text)
        return {"ok": True}


def _app(tg):
    return types.SimpleNamespace(state=types.SimpleNamespace(telegram=tg))


def test_send_manual_ships_the_pdf(tmp_path, monkeypatch):
    from app import main
    pdf = tmp_path / "izi_manual.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(main, "_MANUAL_PDF", pdf)
    tg = _RecTg()
    asyncio.run(main._send_manual(_app(tg)))
    caption, data, filename = tg.docs[0]
    assert data == b"%PDF-1.4 fake"
    assert filename == "izi_manual.pdf"
    assert tg.texts == []


def test_send_manual_missing_file_replies_with_text(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "_MANUAL_PDF", tmp_path / "nope.pdf")
    tg = _RecTg()
    asyncio.run(main._send_manual(_app(tg)))
    assert tg.docs == []
    assert len(tg.texts) == 1 and "build_manual" in tg.texts[0]


def test_manual_listed_in_pinned_help():
    assert "/manual" in format_pinned_help()
