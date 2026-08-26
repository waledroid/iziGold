#!/usr/bin/env python3
"""Regenerate docs/izi_manual.pdf — the operator quick-reference shipped by
the Telegram /manual command.

Run from anywhere with the service venv:
    service/.venv/bin/python3 scripts/build_manual.py
"""
import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[1] / "docs" / "izi_manual.pdf"

# Palette: Institutional Gold & Deep Slate Navy
NAVY_DARK   = colors.HexColor("#0A1128")
NAVY_CARD   = colors.HexColor("#1C2541")
GOLD_ACCENT = colors.HexColor("#D4AF37")
GOLD_DARK   = colors.HexColor("#8C6210")
GOLD_LIGHT  = colors.HexColor("#FEF9E7")
GOLD_BORDER = colors.HexColor("#F9E79F")
SLATE_BG    = colors.HexColor("#F8FAFC")
SLATE_CARD  = colors.HexColor("#F1F5F9")
SLATE_BORDER= colors.HexColor("#CBD5E1")
SLATE_MUTED = colors.HexColor("#64748B")
TEXT_MAIN   = colors.HexColor("#1E293B")
TEXT_LIGHT  = colors.HexColor("#F8FAFC")
CODE_BG     = colors.HexColor("#0B132B")
CODE_TEXT   = colors.HexColor("#38BDF8")
SUCCESS_BG  = colors.HexColor("#F0FDF4")
SUCCESS_TXT = colors.HexColor("#166534")
SUCCESS_BD  = colors.HexColor("#BBF7D0")
WARN_BG     = colors.HexColor("#FEF2F2")
WARN_TXT    = colors.HexColor("#991B1B")
WARN_BD     = colors.HexColor("#FECACA")
BLUE_BG     = colors.HexColor("#EFF6FF")
BLUE_TXT    = colors.HexColor("#1E40AF")
BLUE_BD     = colors.HexColor("#BFDBFE")


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas for total page count and professional headers/footers."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_decorations(self, num_pages):
        self.saveState()
        w, h = A4

        # Top gold accent rule
        self.setFillColor(GOLD_ACCENT)
        self.rect(36, h - 22, w - 72, 2.5, stroke=0, fill=1)

        # Header Text
        self.setFont("Helvetica-Bold", 7.5)
        self.setFillColor(NAVY_CARD)
        self.drawString(36, h - 32, "IZI  |  XAU ASSISTANT")
        self.setFont("Helvetica", 7.5)
        self.setFillColor(SLATE_MUTED)
        self.drawString(135, h - 32, "•   INSTITUTIONAL OPERATOR MANUAL")
        self.drawRightString(w - 36, h - 32, "GOLD / XAUUSD ENGINE")

        # Top separator
        self.setStrokeColor(SLATE_BORDER)
        self.setLineWidth(0.6)
        self.line(36, h - 37, w - 36, h - 37)

        # Bottom separator
        self.line(36, 36, w - 36, 36)

        # Bottom gold bar
        self.setFillColor(GOLD_ACCENT)
        self.rect(36, 34, 45, 2, stroke=0, fill=1)

        # Footer Text
        self.setFont("Helvetica", 7.5)
        self.setFillColor(SLATE_MUTED)
        today = datetime.date.today().isoformat()
        self.drawString(36, 23, f"izi XAU Assistant  •  Confidential Operator Reference  •  Build: {today}")
        self.setFont("Helvetica-Bold", 7.5)
        self.setFillColor(NAVY_CARD)
        self.drawRightString(w - 36, 23, f"Page {self._pageNumber} of {num_pages}")

        self.restoreState()


def get_styles():
    ss = getSampleStyleSheet()
    
    styles = {
        "title": ParagraphStyle(
            "DocTitle",
            fontName="Helvetica-Bold",
            fontSize=13.5,
            leading=16.5,
            textColor=TEXT_LIGHT,
        ),
        "subtitle": ParagraphStyle(
            "DocSubTitle",
            fontName="Helvetica",
            fontSize=7.8,
            leading=10,
            textColor=GOLD_ACCENT,
        ),
        "tag_right": ParagraphStyle(
            "DocTagRight",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9.5,
            textColor=GOLD_ACCENT,
            alignment=2,
        ),
        "tag_sub": ParagraphStyle(
            "DocTagSub",
            fontName="Helvetica",
            fontSize=7,
            leading=9,
            textColor=TEXT_LIGHT,
            alignment=2,
        ),
        "section_title": ParagraphStyle(
            "SecTitle",
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=12.5,
            textColor=NAVY_DARK,
            spaceBefore=0,
            spaceAfter=0,
        ),
        "card_title": ParagraphStyle(
            "CardTitle",
            fontName="Helvetica-Bold",
            fontSize=8.2,
            leading=10.5,
            textColor=NAVY_DARK,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName="Helvetica",
            fontSize=7.2,
            leading=10,
            textColor=TEXT_MAIN,
        ),
        "body_bold": ParagraphStyle(
            "BodyBold",
            fontName="Helvetica-Bold",
            fontSize=7.2,
            leading=10,
            textColor=TEXT_MAIN,
        ),
        "body_muted": ParagraphStyle(
            "BodyMuted",
            fontName="Helvetica",
            fontSize=6.8,
            leading=9,
            textColor=SLATE_MUTED,
        ),
        "code_block": ParagraphStyle(
            "CodeBlock",
            fontName="Courier-Bold",
            fontSize=7.2,
            leading=9.5,
            textColor=CODE_TEXT,
        ),
        "table_header": ParagraphStyle(
            "THeader",
            fontName="Helvetica-Bold",
            fontSize=7.2,
            leading=9,
            textColor=TEXT_LIGHT,
        ),
        "table_cell": ParagraphStyle(
            "TCell",
            fontName="Helvetica",
            fontSize=7.0,
            leading=9.3,
            textColor=TEXT_MAIN,
        ),
        "table_cell_bold": ParagraphStyle(
            "TCellBold",
            fontName="Helvetica-Bold",
            fontSize=7.0,
            leading=9.3,
            textColor=NAVY_DARK,
        ),
        "table_cmd": ParagraphStyle(
            "TCmd",
            fontName="Courier-Bold",
            fontSize=7.2,
            leading=9.2,
            textColor=NAVY_DARK,
        ),
    }
    return styles


def make_hero_banner(styles, title_text, category_tag, subtitle_text):
    """Creates a dark slate & gold hero banner card."""
    content = [
        [
            Paragraph(title_text, styles["title"]),
            Paragraph(f"<b>{category_tag.upper()}</b>", styles["tag_right"]),
        ],
        [
            Paragraph(subtitle_text, styles["subtitle"]),
            Paragraph("TELEGRAM COMPANION: <b>/manual</b>", styles["tag_sub"]),
        ]
    ]
    t = Table(content, colWidths=[355, 168.27])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY_DARK),
        ('PADDING', (0,0), (-1,-1), 6),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW', (0,0), (-1,0), 0.5, NAVY_CARD),
        ('BOTTOMPADDING', (0,0), (-1,0), 2.5),
        ('TOPPADDING', (0,1), (-1,1), 2.5),
    ]))
    return t


def make_card(styles, title, body_paragraphs, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=None, width=523.27, padding=5):
    """Creates a styled card container."""
    flowables = []
    if title:
        title_p = Paragraph(title, styles["card_title"])
        flowables.append(title_p)
        flowables.append(Spacer(1, 2))
    for p in body_paragraphs:
        if isinstance(p, str):
            flowables.append(Paragraph(p, styles["body"]))
            flowables.append(Spacer(1, 1.5))
        else:
            flowables.append(p)
            flowables.append(Spacer(1, 1.5))
    if flowables and isinstance(flowables[-1], Spacer):
        flowables.pop()

    t = Table([[flowables]], colWidths=[width])
    style_cmds = [
        ('BACKGROUND', (0,0), (-1,-1), bg_color),
        ('BOX', (0,0), (-1,-1), 0.6, border_color),
        ('PADDING', (0,0), (-1,-1), padding),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]
    if accent_color:
        style_cmds.append(('LINELEFT', (0,0), (0,-1), 3.0, accent_color))
    t.setStyle(TableStyle(style_cmds))
    return t


def make_code_box(styles, lines, width=523.27):
    """Creates a sleek terminal-style code snippet block."""
    text = "<br/>".join([f"<font color='#38BDF8'>{line}</font>" for line in lines])
    p = Paragraph(text, styles["code_block"])
    t = Table([[p]], colWidths=[width])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), CODE_BG),
        ('BOX', (0,0), (-1,-1), 0.6, NAVY_CARD),
        ('PADDING', (0,0), (-1,-1), 4.5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LINELEFT', (0,0), (0,-1), 2.5, GOLD_ACCENT),
    ]))
    return t


def build_pdf():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=42,
        bottomMargin=42,
    )
    styles = get_styles()
    story = []
    col_w = (523.27 - 8) / 2

    # =========================================================================
    # PAGE 1: SYSTEM OVERVIEW & CORE ARCHITECTURE
    # =========================================================================
    story.append(make_hero_banner(
        styles,
        "izi — XAU Assistant",
        "Section 01 // Architecture",
        "Autonomous MT5 Execution Engine + AI Forecaster + Telegram Operations"
    ))
    story.append(Spacer(1, 5))

    # Executive Overview
    overview_text = (
        "<b>izi</b> is a high-performance, institutional-grade XAUUSD (Gold) trading assistant "
        "engineered in two resilient halves. The <b>MQL5 Expert Advisor</b> (running inside MetaTrader 5 "
        "on Windows) executes real-time strategy triggers and manages live baskets. The <b>Python Engine</b> "
        "(FastAPI in WSL2) grades signals via deep-learning forecasting, streams telemetry to Telegram, "
        "and coordinates remote commands."
    )
    story.append(make_card(styles, None, [overview_text], bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=GOLD_ACCENT, padding=4.5))
    story.append(Spacer(1, 5))

    # Two Golden Laws (2-column layout)
    law1_content = [
        "<font color='#8C6210'><b>LAW 1: STRATEGY DECIDES, AI GRADES</b></font>",
        "The MQL5 EA is the <b>sole decision maker</b>. On closed bar, the EA evaluates entry criteria and executes <i>first</i> in AUTO mode, then queries AI forecasting. Trading decisions never wait on inference latency."
    ]
    law2_content = [
        "<font color='#1E40AF'><b>LAW 2: FAIL-OPEN RESILIENCE</b></font>",
        "Strict fail-open design across all layers: if the AI service or network is offline, strategy signals continue to alert and execute automatically, flagged <i>'AI unavailable'</i>. Service downtime never blocks a trade."
    ]
    c1 = make_card(styles, None, law1_content, bg_color=GOLD_LIGHT, border_color=GOLD_BORDER, accent_color=GOLD_DARK, width=col_w, padding=4.5)
    c2 = make_card(styles, None, law2_content, bg_color=BLUE_BG, border_color=BLUE_BD, accent_color=BLUE_TXT, width=col_w, padding=4.5)
    t_laws = Table([[c1, c2]], colWidths=[col_w, col_w])
    t_laws.setStyle(TableStyle([
        ('PADDING', (0,0), (-1,-1), 0),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 8),
    ]))
    story.append(t_laws)
    story.append(Spacer(1, 5))

    # Moving Parts Architecture Matrix
    story.append(Paragraph("SYSTEM TOPOLOGY & INTEGRATION MATRIX", styles["section_title"]))
    story.append(Spacer(1, 2.5))

    top_data = [
        [
            Paragraph("<b>Component</b>", styles["table_header"]),
            Paragraph("<b>Environment / Host</b>", styles["table_header"]),
            Paragraph("<b>Core Responsibility</b>", styles["table_header"]),
            Paragraph("<b>Port / Location</b>", styles["table_header"]),
        ],
        [
            Paragraph("<b>MQL5 EA</b>", styles["table_cell_bold"]),
            Paragraph("Windows (MT5 Terminal)", styles["table_cell"]),
            Paragraph("Closed-bar signal evaluation, AUTO order execution, basket trail & ratchet management", styles["table_cell"]),
            Paragraph("<code>XauAssistant.mq5</code>", styles["table_cmd"]),
        ],
        [
            Paragraph("<b>FastAPI Engine</b>", styles["table_cell_bold"]),
            Paragraph("WSL2 (Python 3.13 venv)", styles["table_cell"]),
            Paragraph("Chronos AI signal grading, news event blackout windows, web mini-app, remote router", styles["table_cell"]),
            Paragraph("<code>http://127.0.0.1:9000</code>", styles["table_cmd"]),
        ],
        [
            Paragraph("<b>SQLite DB</b>", styles["table_cell_bold"]),
            Paragraph("WSL2 Filesystem", styles["table_cell"]),
            Paragraph("Zero-loss audit store: ticks, candles, strategy proposals, AI verdicts & trade logs", styles["table_cell"]),
            Paragraph("<code>xau_assistant.db</code>", styles["table_cmd"]),
        ],
        [
            Paragraph("<b>Telegram Bot</b>", styles["table_cell_bold"]),
            Paragraph("Cloud API / Webhooks", styles["table_cell"]),
            Paragraph("Instant push alerts, interactive action buttons ([BUY], [SELL], [EXIT]), mini-app charts", styles["table_cell"]),
            Paragraph("<code>@izi_assistant_bot</code>", styles["table_cmd"]),
        ],
    ]
    t_top = Table(top_data, colWidths=[80, 110, 208.27, 125])
    t_top.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY_CARD),
        ('PADDING', (0,0), (-1,-1), 3.5),
        ('GRID', (0,0), (-1,-1), 0.5, SLATE_BORDER),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, SLATE_BG]),
    ]))
    story.append(t_top)
    story.append(Spacer(1, 5))

    # Core Execution Lifecycle Box (2 columns)
    hb_left = [
        "<b>Bi-directional Heartbeat Pipeline</b>",
        "The MQL5 EA heartbeats the FastAPI service every <b>5 seconds</b>. "
        "Remote operator actions (manual trades, basket exits, stop-loss ratchets) queue in memory and ride back "
        "on the immediate next heartbeat payload. Tap latency to MT5 terminal is <b>&le; 5 seconds</b>."
    ]
    hb_right = [
        "<b>Every-Bar Lazy Resolution</b>",
        "The EA sends candle data to <code>/analyze</code> on every closed bar (including <b>NONE</b> signals). "
        "This allows the service to lazily evaluate outcomes of past signals without maintaining a separate data feed, "
        "logging full statistical telemetry into SQLite."
    ]
    c_hb1 = make_card(styles, None, hb_left, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=NAVY_DARK, width=col_w, padding=4.5)
    c_hb2 = make_card(styles, None, hb_right, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=GOLD_ACCENT, width=col_w, padding=4.5)
    t_hb = Table([[c_hb1, c_hb2]], colWidths=[col_w, col_w])
    t_hb.setStyle(TableStyle([
        ('PADDING', (0,0), (-1,-1), 0),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 8),
    ]))
    story.append(t_hb)
    story.append(Spacer(1, 5))

    # Architectural Rules Footer Card
    rules_summary = (
        "<b>Core Architectural Rules:</b> (1) No martingale: position size never increases after a loss; pyramiding adds strictly into winning legs. "
        "(2) Risk state survives crashes via MT5 Global Variables. (3) AI confidence grading is logged from tick zero to ensure empirical evaluation."
    )
    story.append(make_card(styles, None, [rules_summary], bg_color=SLATE_CARD, border_color=SLATE_BORDER, accent_color=SLATE_MUTED, padding=4.5))

    # =========================================================================
    # PAGE 2: CLIENT SETUP & CONFIGURATION
    # =========================================================================
    story.append(PageBreak())
    story.append(make_hero_banner(
        styles,
        "Client Setup & Configuration",
        "Section 02 // Setup & Params",
        "Idempotent Single-Script Deployment & Terminal Integration"
    ))
    story.append(Spacer(1, 5))

    # Automated Setup Script
    story.append(Paragraph("PHASE 1: AUTOMATED ZERO-TOUCH SETUP", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    story.append(Paragraph("A single unified script configures the entire stack. Safe to re-run at any time (idempotent; skips completed phases):", styles["body"]))
    story.append(Spacer(1, 1.5))
    story.append(make_code_box(styles, ["cd <repo_root> && scripts/setup.sh"]))
    story.append(Spacer(1, 1.5))
    setup_features = (
        "• <b>Automated Actions:</b> Provisions Python venv, installs core dependencies, validates 600+ test suites, "
        "starts background service, pairs Telegram bot (message your bot once when prompted), copies MQL5 EA to MT5 data path and compiles it."
    )
    story.append(Paragraph(setup_features, styles["body_muted"]))
    story.append(Spacer(1, 4.5))

    # Configuration .env Table
    story.append(Paragraph("PHASE 2: SERVICE CONFIGURATION (.env)", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    story.append(make_code_box(styles, ["cd service && cp .env.example .env"]))
    story.append(Spacer(1, 1.5))

    env_table_data = [
        [
            Paragraph("<b>Variable</b>", styles["table_header"]),
            Paragraph("<b>Default / Options</b>", styles["table_header"]),
            Paragraph("<b>Description & Operational Impact</b>", styles["table_header"]),
        ],
        [
            Paragraph("<code>FORECASTER</code>", styles["table_cmd"]),
            Paragraph("<code>chronos</code> | <code>fake</code>", styles["table_cell"]),
            Paragraph("<code>chronos</code> loads real neural weights. <code>fake</code> uses fast linear extrapolation for headless testing.", styles["table_cell"]),
        ],
        [
            Paragraph("<code>PORT / HOST</code>", styles["table_cmd"]),
            Paragraph("<code>9000 / 127.0.0.1</code>", styles["table_cell"]),
            Paragraph("Service port and localhost binding. Keep 127.0.0.1 to avoid exposing credentials to local LAN.", styles["table_cell"]),
        ],
        [
            Paragraph("<code>TELEGRAM_BOT_TOKEN</code>", styles["table_cmd"]),
            Paragraph("<code>token string</code>", styles["table_cell"]),
            Paragraph("API token obtained from @BotFather for alerts, commands, and web mini-app charts.", styles["table_cell"]),
        ],
    ]
    t_env = Table(env_table_data, colWidths=[120, 110, 293.27])
    t_env.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY_CARD),
        ('PADDING', (0,0), (-1,-1), 2.8),
        ('GRID', (0,0), (-1,-1), 0.5, SLATE_BORDER),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, SLATE_BG]),
    ]))
    story.append(t_env)
    story.append(Spacer(1, 4.5))

    # Manual MT5 Steps (Cards)
    story.append(Paragraph("PHASE 3: METATRADER 5 TERMINAL INTEGRATION (MANDATORY)", styles["section_title"]))
    story.append(Spacer(1, 2))

    step1_box = [
        "<b>Step 1: Whitelist WebRequest URL in MT5</b>",
        "Navigate to: <b>Tools &gt; Options &gt; Expert Advisors</b>",
        "1. Check the box: <i>'Allow WebRequest for listed URL'</i>",
        "2. Click green <b>+</b> and add exactly: <code>http://127.0.0.1:9000</code>",
        "<i>(MT5 stores this encrypted; requires manual entry)</i>"
    ]
    step2_box = [
        "<b>Step 2: Attach EA & Enable Algo Trading</b>",
        "1. In <b>Navigator &gt; Expert Advisors</b>, drag <code>XauAssistant</code> onto a <b>XAUUSD M5</b> chart.",
        "2. In the dialog, check <i>'Allow Algo Trading'</i>.",
        "3. Click <b>Algo Trading</b> button in MT5 top toolbar (ensure icon turns green).",
        "<i>(If chart was already open, remove and re-attach the EA)</i>"
    ]
    c_s1 = make_card(styles, None, step1_box, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=BLUE_TXT, width=col_w, padding=4)
    c_s2 = make_card(styles, None, step2_box, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=GOLD_DARK, width=col_w, padding=4)
    t_steps = Table([[c_s1, c_s2]], colWidths=[col_w, col_w])
    t_steps.setStyle(TableStyle([
        ('PADDING', (0,0), (-1,-1), 0),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 8),
    ]))
    story.append(t_steps)
    story.append(Spacer(1, 4.5))

    # Phase 4: Key EA Parameters Table
    story.append(Paragraph("PHASE 4: KEY EA INPUT PARAMETERS REFERENCE", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    ea_param_data = [
        [
            Paragraph("<b>Parameter</b>", styles["table_header"]),
            Paragraph("<b>Default</b>", styles["table_header"]),
            Paragraph("<b>Function & Optimization Context</b>", styles["table_header"]),
        ],
        [
            Paragraph("<code>ExecutionMode</code>", styles["table_cmd"]),
            Paragraph("<code>AUTO</code>", styles["table_cell"]),
            Paragraph("<code>AUTO</code> (executes on closed bar) vs <code>MANUAL</code> (proposes with Telegram buttons).", styles["table_cell"]),
        ],
        [
            Paragraph("<code>EntryMode</code>", styles["table_cmd"]),
            Paragraph("<code>ADR</code>", styles["table_cell"]),
            Paragraph("<code>ADR</code> (1/3 ADR target + partial trailing) vs <code>FIXED</code> (3R runner + SL ratchet).", styles["table_cell"]),
        ],
        [
            Paragraph("<code>RiskPercent</code>", styles["table_cmd"]),
            Paragraph("<code>1.0%</code>", styles["table_cell"]),
            Paragraph("Risk per trade basket as % of account balance. Auto-sizes lots via ATR stop distance.", styles["table_cell"]),
        ],
        [
            Paragraph("<code>MaxSpreadPoints</code>", styles["table_cmd"]),
            Paragraph("<code>40</code>", styles["table_cell"]),
            Paragraph("Spread filter threshold in points. Rejects trade entries during volatility widenings.", styles["table_cell"]),
        ],
    ]
    t_params = Table(ea_param_data, colWidths=[120, 75, 328.27])
    t_params.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY_CARD),
        ('PADDING', (0,0), (-1,-1), 2.8),
        ('GRID', (0,0), (-1,-1), 0.5, SLATE_BORDER),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, SLATE_BG]),
    ]))
    story.append(t_params)
    story.append(Spacer(1, 4.5))

    # Live Trading Safety Interlock
    live_warning = [
        "<font color='#991B1B'><b>CRITICAL SAFETY LOCK: REAL-MONEY LIVE ACCOUNTS</b></font>",
        "On live accounts (real currency), AUTO mode is strictly locked out by default and will refuse trades "
        "until the EA input parameter <b><code>AllowLiveTrading = true</code></b> is explicitly enabled. "
        "This intentional safeguard protects capital until paper trading and AI accuracy telemetry earn live execution."
    ]
    story.append(make_card(styles, None, live_warning, bg_color=WARN_BG, border_color=WARN_BD, accent_color=WARN_TXT, padding=4.5))

    # =========================================================================
    # PAGE 3: DAILY OPERATIONS & LIFECYCLE
    # =========================================================================
    story.append(PageBreak())
    story.append(make_hero_banner(
        styles,
        "Daily Operations & Lifecycle",
        "Section 03 // Operations & Telemetry",
        "Launch Procedures, Telemetry Checks & Safe Maintenance"
    ))
    story.append(Spacer(1, 5))

    # Launch Methods
    story.append(Paragraph("1. SYSTEM STARTUP & ORCHESTRATION", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    launch_text = (
        "<b>Standard 1-Click Launch:</b> Double-click <code>xau-launch.bat</code> on the Desktop — "
        "it automatically initialises WSL2, starts the FastAPI service, and launches MetaTrader 5."
    )
    story.append(make_card(styles, None, [launch_text], bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=GOLD_ACCENT, padding=3.5))
    story.append(Spacer(1, 2))
    story.append(Paragraph("<b>Headless / Manual Startup (WSL2):</b>", styles["body_bold"]))
    story.append(Spacer(1, 1.5))
    story.append(make_code_box(styles, [
        "cd <repo_root>/service",
        "nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9000 >> service.log 2>&1 &"
    ]))
    story.append(Spacer(1, 1.5))
    story.append(Paragraph("<i>Note: Initial neural AI forecast on startup may take ~1-2 min for weight loading. Trading is never blocked (fail-open).</i>", styles["body_muted"]))
    story.append(Spacer(1, 4.5))

    # Health Verification (2 Columns)
    story.append(Paragraph("2. HEALTH MONITORING & TELEMETRY CHECKS", styles["section_title"]))
    story.append(Spacer(1, 1.5))

    health_api = [
        "<b>REST API Endpoint</b>",
        "<code>curl http://127.0.0.1:9000/health</code>",
        "Returns JSON payload confirming database read/write, forecaster readiness, and memory utilization."
    ]
    health_tg = [
        "<b>Telegram Live Snapshot</b>",
        "Run <b><code>/status</code></b> in Telegram.",
        "Line 1 displays EA link health (e.g. <code>EA: connected (2s ago)</code>), active mode, and shield statuses."
    ]
    c_h1 = make_card(styles, None, health_api, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=BLUE_TXT, width=col_w, padding=4)
    c_h2 = make_card(styles, None, health_tg, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=SUCCESS_TXT, width=col_w, padding=4)
    t_health = Table([[c_h1, c_h2]], colWidths=[col_w, col_w])
    t_health.setStyle(TableStyle([
        ('PADDING', (0,0), (-1,-1), 0),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 8),
    ]))
    story.append(t_health)
    story.append(Spacer(1, 4.5))

    # Shutdown & Persistence
    story.append(Paragraph("3. CONTROLLED SHUTDOWN & STATE SURVIVAL", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    story.append(make_code_box(styles, ["pkill -f \"uvicorn app.main\"   # Exit code 144 is standard and clean"]))
    story.append(Spacer(1, 1.5))
    shutdown_text = (
        "<b>Persistent Risk State:</b> Critical risk parameters (daily loss brake, drawdown high-water mark, "
        "kill switch) are stored in <b>MT5 Global Variables</b>, not volatile RAM. They survive service crashes, "
        "terminal restarts, and PC reboots seamlessly."
    )
    story.append(make_card(styles, None, [shutdown_text], bg_color=SUCCESS_BG, border_color=SUCCESS_BD, accent_color=SUCCESS_TXT, padding=4))
    story.append(Spacer(1, 4.5))

    # Multi-Tier Risk Defense Grid
    story.append(Paragraph("4. MULTI-TIER RISK DEFENSE ARCHITECTURE", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    risk_tiers = [
        [
            Paragraph("<b>Tier</b>", styles["table_header"]),
            Paragraph("<b>Mechanism & Scope</b>", styles["table_header"]),
            Paragraph("<b>Trigger & Action</b>", styles["table_header"]),
        ],
        [
            Paragraph("<b>Tier 1: Per-Trade</b>", styles["table_cell_bold"]),
            Paragraph("MoneyWatch dynamic lot sizing + ATR stops", styles["table_cell"]),
            Paragraph("Sizes each order to exact risk %; enforces hard stop-loss on every leg.", styles["table_cell"]),
        ],
        [
            Paragraph("<b>Tier 2: Daily Brake</b>", styles["table_cell_bold"]),
            Paragraph("Cumulative daily realized loss brake", styles["table_cell"]),
            Paragraph("Alerts at &ge;70% spent; halts all new entries at 100% until 00:00 server time.", styles["table_cell"]),
        ],
        [
            Paragraph("<b>Tier 3: Kill Switch</b>", styles["table_cell_bold"]),
            Paragraph("Equity drawdown high-water mark protection", styles["table_cell"]),
            Paragraph("Terminal locks if total equity drops below absolute max drawdown watermark.", styles["table_cell"]),
        ],
    ]
    t_risk_tiers = Table(risk_tiers, colWidths=[100, 200, 223.27])
    t_risk_tiers.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY_CARD),
        ('PADDING', (0,0), (-1,-1), 2.8),
        ('GRID', (0,0), (-1,-1), 0.5, SLATE_BORDER),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, SLATE_BG]),
    ]))
    story.append(t_risk_tiers)
    story.append(Spacer(1, 4.5))

    # Code Reloads & Strategy Maintenance
    story.append(Paragraph("5. HOT UPDATES & RECOMPILATION PROTOCOL", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    reload_steps = (
        "• <b>Python Service Updates:</b> Kill first (foreground <code>pkill</code>), start second (background <code>nohup</code>). Never combine in one command.<br/>"
        "• <b>MQL5 EA Updates:</b> Run <code>scripts/setup.sh</code> — it copies files and triggers MetaEditor compile. "
        "MT5 hot-reloads the newly compiled EA automatically upon completion with 0 errors.<br/>"
        "• <b>Strategy Changes:</b> New strategies slot cleanly into <code>mt5/Include/XauAssistant/Strategies/</code> behind <code>CStrategy</code> without touching execution logic."
    )
    story.append(make_card(styles, None, [reload_steps], bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=NAVY_DARK, padding=4))

    # =========================================================================
    # PAGE 4: TELEGRAM COMMAND CENTER
    # =========================================================================
    story.append(PageBreak())
    story.append(make_hero_banner(
        styles,
        "Telegram Command Center",
        "Section 04 // Commands & Mini-App",
        "Comprehensive Operator Command Directory & Manual Trading Pipeline"
    ))
    story.append(Spacer(1, 5))

    story.append(Paragraph("OPERATOR COMMAND DIRECTORY", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    story.append(Paragraph("Commands respond exclusively to the authorized owner chat. Broadcast channels receive sanitized alerts without financial balances or interactive buttons.", styles["body_muted"]))
    story.append(Spacer(1, 2.5))

    cmd_data = [
        [
            Paragraph("<b>Command</b>", styles["table_header"]),
            Paragraph("<b>Category</b>", styles["table_header"]),
            Paragraph("<b>Description & Functionality</b>", styles["table_header"]),
            Paragraph("<b>Output / Behavior</b>", styles["table_header"]),
        ],
        [
            Paragraph("<code>/status</code>", styles["table_cmd"]),
            Paragraph("<font color='#1E40AF'>Telemetry</font>", styles["table_cell"]),
            Paragraph("Complete health snapshot: EA heartbeat, mini-app, news blackout & risk shields.", styles["table_cell"]),
            Paragraph("Status report card", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/bal</code>", styles["table_cmd"]),
            Paragraph("<font color='#1E40AF'>Telemetry</font>", styles["table_cell"]),
            Paragraph("Account balance, live equity, floating P/L, daily & weekly realized performance.", styles["table_cell"]),
            Paragraph("Financial metrics", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/mode</code>", styles["table_cmd"]),
            Paragraph("<font color='#8C6210'>Config</font>", styles["table_cell"]),
            Paragraph("Toggle execution mode (AUTO / MANUAL), target mode (ADR / FIXED), and lane.", styles["table_cell"]),
            Paragraph("Active configuration", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/agree</code>", styles["table_cmd"]),
            Paragraph("<font color='#8C6210'>Config</font>", styles["table_cell"]),
            Paragraph("Toggle confirmation filters: Higher-Timeframe bias & EMA-200 trend agreement.", styles["table_cell"]),
            Paragraph("Filter status", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/trade</code>", styles["table_cmd"]),
            Paragraph("<font color='#166534'>Execution</font>", styles["table_cell"]),
            Paragraph("Request manual order entry menu with live gold quote and <b>[BUY]</b> / <b>[SELL]</b> buttons.", styles["table_cell"]),
            Paragraph("Interactive buttons", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/news</code>", styles["table_cmd"]),
            Paragraph("<font color='#991B1B'>Risk</font>", styles["table_cell"]),
            Paragraph("Upcoming high-impact USD economic events and active entry blackout windows.", styles["table_cell"]),
            Paragraph("Economic calendar", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/chart</code>", styles["table_cmd"]),
            Paragraph("<font color='#1E40AF'>Visuals</font>", styles["table_cell"]),
            Paragraph("Launches Telegram Web Mini-App live chart or renders a static candle snapshot.", styles["table_cell"]),
            Paragraph("Interactive chart", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/stats</code>", styles["table_cmd"]),
            Paragraph("<font color='#1E40AF'>Analytics</font>", styles["table_cell"]),
            Paragraph("Detailed hit rates, expectancy, and win/loss performance per strategy module.", styles["table_cell"]),
            Paragraph("Performance breakdown", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/history</code>", styles["table_cmd"]),
            Paragraph("<font color='#1E40AF'>Audit</font>", styles["table_cell"]),
            Paragraph("Audit trail of the last 10 execution events, entries, exits, and SL ratchets.", styles["table_cell"]),
            Paragraph("Recent trade logs", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/channel</code>", styles["table_cmd"]),
            Paragraph("<font color='#64748B'>Config</font>", styles["table_cell"]),
            Paragraph("Link or unlink an external Telegram broadcast channel for public trade signals.", styles["table_cell"]),
            Paragraph("Channel binding", styles["table_cell"]),
        ],
        [
            Paragraph("<code>/manual</code>", styles["table_cmd"]),
            Paragraph("<font color='#64748B'>Docs</font>", styles["table_cell"]),
            Paragraph("Instantly compiles and delivers this operator quick-reference PDF to chat.", styles["table_cell"]),
            Paragraph("PDF document", styles["table_cell"]),
        ],
    ]
    t_cmd = Table(cmd_data, colWidths=[65, 55, 265, 138.27])
    t_cmd.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY_CARD),
        ('PADDING', (0,0), (-1,-1), 2.5),
        ('GRID', (0,0), (-1,-1), 0.5, SLATE_BORDER),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, SLATE_BG]),
    ]))
    story.append(t_cmd)
    story.append(Spacer(1, 4))

    # Manual Trade Pipeline & Mini-App Grid (2 Columns)
    trade_steps = [
        "<b>Manual Trade Pipeline (/trade)</b>",
        "<b>1. Operator Tap:</b> <code>/trade</code> displays live bid/ask with <b>[BUY]</b> / <b>[SELL]</b> buttons.<br/>"
        "<b>2. Multi-Gate Check:</b> Verifies no active basket, no queued entry, and EA connected.<br/>"
        "<b>3. Heartbeat Dispatch:</b> Next 5s heartbeat routes order to MT5.<br/>"
        "<b>4. Basket Trail:</b> Position managed automatically with target exits."
    ]

    mini_app_box = [
        "<b>Telegram Web Mini-App (/chart)</b>",
        "• <b>Real-Time Charts:</b> Full TradingView lightweight candle charts with HalfTrend + EMA overlays.<br/>"
        "• <b>AI Verdict Flags:</b> Real-time visual pins showing neural forecaster confidence & direction.<br/>"
        "• <b>Emergency Exit Button:</b> 1-tap full basket close directly from the Web App interface."
    ]

    c_tr = make_card(styles, None, trade_steps, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=SUCCESS_TXT, width=col_w, padding=4)
    c_ma = make_card(styles, None, mini_app_box, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=BLUE_TXT, width=col_w, padding=4)
    t_grid4 = Table([[c_tr, c_ma]], colWidths=[col_w, col_w])
    t_grid4.setStyle(TableStyle([
        ('PADDING', (0,0), (-1,-1), 0),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 8),
    ]))
    story.append(t_grid4)
    story.append(Spacer(1, 4))

    # Broadcast Security Footer
    sec_notice = (
        "<b>Owner Security & Channel Isolation:</b> Linked broadcast channels (<code>/channel</code>) receive sanitized trade "
        "execution announcements for subscribers. Account balances, equity figures, risk metrics, and interactive buttons are "
        "strictly withheld. Only the authenticated owner chat retains remote execution authorization."
    )
    story.append(make_card(styles, None, [sec_notice], bg_color=SLATE_CARD, border_color=SLATE_BORDER, accent_color=GOLD_DARK, padding=4))

    # =========================================================================
    # PAGE 5: CONTROLS, RISK & TROUBLESHOOTING
    # =========================================================================
    story.append(PageBreak())
    story.append(make_hero_banner(
        styles,
        "Controls, Risk & Troubleshooting",
        "Section 05 // Buttons & Diagnostics",
        "Button Behaviors, Protection Shields & Quick Diagnostics Matrix"
    ))
    story.append(Spacer(1, 5))

    # Buttons Reference
    story.append(Paragraph("INTERACTIVE BUTTON DIRECTORY", styles["section_title"]))
    story.append(Spacer(1, 1.5))

    btn_data = [
        [
            Paragraph("<b>Button Label</b>", styles["table_header"]),
            Paragraph("<b>Context / Trigger</b>", styles["table_header"]),
            Paragraph("<b>System Action & Safety Rule</b>", styles["table_header"]),
        ],
        [
            Paragraph("<b>[ Take trade ] / [ Skip ]</b>", styles["table_cell_bold"]),
            Paragraph("MANUAL mode proposals", styles["table_cell"]),
            Paragraph("Approves or rejects pending strategy setup. Auto-expires safely after 15 minutes.", styles["table_cell"]),
        ],
        [
            Paragraph("<b>[ EXIT - close trade ]</b>", styles["table_cell_bold"]),
            Paragraph("Trade entry notification", styles["table_cell"]),
            Paragraph("Immediately closes all open basket positions at market on next heartbeat.", styles["table_cell"]),
        ],
        [
            Paragraph("<b>[ Move SL to here ]</b>", styles["table_cell_bold"]),
            Paragraph("FIXED-ride target alert", styles["table_cell"]),
            Paragraph("<b>One-way ratchet:</b> tightens stops of all legs to current price. Never loosens.", styles["table_cell"]),
        ],
        [
            Paragraph("<b>[ Reset brake ]</b>", styles["table_cell_bold"]),
            Paragraph("Daily loss brake notice", styles["table_cell"]),
            Paragraph("Overrides daily loss halt after manual operator risk assessment (&ge;70% spent).", styles["table_cell"]),
        ],
    ]
    t_btn = Table(btn_data, colWidths=[130, 130, 263.27])
    t_btn.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY_CARD),
        ('PADDING', (0,0), (-1,-1), 3.2),
        ('GRID', (0,0), (-1,-1), 0.5, SLATE_BORDER),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, SLATE_BG]),
    ]))
    story.append(t_btn)
    story.append(Spacer(1, 4.5))

    # Risk Shields & Troubleshooting (2 Columns)
    story.append(Paragraph("RISK SHIELDS & DIAGNOSTIC MATRIX", styles["section_title"]))
    story.append(Spacer(1, 2))

    risk_box = [
        "<b>Automated Risk Shields</b>",
        "• <b>USD News Blackout:</b> Freezes new entries <b>30 min before and after</b> high-impact releases. Alerts sent 35m ahead. Exits never blocked.",
        "• <b>Daily Loss Brake:</b> Halts new entries if daily loss reaches threshold. Prevents tilt.",
        "• <b>Spread Gate:</b> Blocks entries during spread spikes (>40 pts)."
    ]

    diag_box = [
        "<b>Quick Diagnostics</b>",
        "• <b>EA Disconnected:</b> Check MT5 is open, EA on chart (smiley), Algo Trading enabled, WebRequest URL set.",
        "• <b>Indicators Missing:</b> MT5 indicators redraw on next live tick; normal during market closure.",
        "• <b>Trade Didn't Fire:</b> Check <code>/status</code> for news blackout, spread gate, or daily loss brake."
    ]

    c_risk = make_card(styles, None, risk_box, bg_color=WARN_BG, border_color=WARN_BD, accent_color=WARN_TXT, width=col_w, padding=4)
    c_diag = make_card(styles, None, diag_box, bg_color=SLATE_BG, border_color=SLATE_BORDER, accent_color=BLUE_TXT, width=col_w, padding=4)
    t_diag = Table([[c_risk, c_diag]], colWidths=[col_w, col_w])
    t_diag.setStyle(TableStyle([
        ('PADDING', (0,0), (-1,-1), 0),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 8),
    ]))
    story.append(t_diag)
    story.append(Spacer(1, 4.5))

    # Diagnostic Table: Symptom -> Root Cause -> Remediation
    story.append(Paragraph("COMMON OPERATOR INCIDENTS & REMEDIATION", styles["section_title"]))
    story.append(Spacer(1, 1.5))

    inc_data = [
        [
            Paragraph("<b>Observed Incident</b>", styles["table_header"]),
            Paragraph("<b>Probable Root Cause</b>", styles["table_header"]),
            Paragraph("<b>Immediate Remediation Action</b>", styles["table_header"]),
        ],
        [
            Paragraph("EA status disconnected", styles["table_cell_bold"]),
            Paragraph("MT5 terminal restarted or EA detached", styles["table_cell"]),
            Paragraph("Re-attach <code>XauAssistant</code> to XAUUSD chart and confirm smiley icon.", styles["table_cell"]),
        ],
        [
            Paragraph("Trade rejected: Live Lock", styles["table_cell_bold"]),
            Paragraph("<code>AllowLiveTrading</code> set to false", styles["table_cell"]),
            Paragraph("In EA Inputs, set <code>AllowLiveTrading = true</code> for real accounts.", styles["table_cell"]),
        ],
        [
            Paragraph("Trade rejected: Spread Spike", styles["table_cell_bold"]),
            Paragraph("Market spread exceeds max threshold", styles["table_cell"]),
            Paragraph("Wait for volatility/spread normalization during high-volume sessions.", styles["table_cell"]),
        ],
    ]
    t_inc = Table(inc_data, colWidths=[130, 150, 243.27])
    t_inc.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY_CARD),
        ('PADDING', (0,0), (-1,-1), 2.8),
        ('GRID', (0,0), (-1,-1), 0.5, SLATE_BORDER),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, SLATE_BG]),
    ]))
    story.append(t_inc)
    story.append(Spacer(1, 4.5))

    # System File Locations Reference
    story.append(Paragraph("CRITICAL LOG & STORAGE PATHS", styles["section_title"]))
    story.append(Spacer(1, 1.5))
    file_data = [
        [
            Paragraph("<code>service/service.log</code>", styles["table_cmd"]),
            Paragraph("FastAPI service runtime log (auto-rotated at 20 MB with rollover backups).", styles["table_cell"]),
        ],
        [
            Paragraph("<code>service/xau_assistant.db</code>", styles["table_cmd"]),
            Paragraph("SQLite database containing signals, trade fills, heartbeats and forecaster grades.", styles["table_cell"]),
        ],
        [
            Paragraph("<code>MT5 'Experts' Tab</code>", styles["table_cmd"]),
            Paragraph("MetaTrader 5 terminal log showing raw tick processing, order tickets, and MQL5 alerts.", styles["table_cell"]),
        ],
    ]
    t_file = Table(file_data, colWidths=[150, 373.27])
    t_file.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, SLATE_BORDER),
        ('PADDING', (0,0), (-1,-1), 2.8),
        ('BACKGROUND', (0,0), (0,-1), SLATE_BG),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_file)

    # Build document
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Generated {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build_pdf()
