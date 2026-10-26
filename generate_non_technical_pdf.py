"""
Script to generate a non-technical, executive PDF guide using ReportLab.
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak, HRFlowable
)
from reportlab.pdfgen import canvas
import sys
from pathlib import Path

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super(NumberedCanvas, self).showPage()
        super(NumberedCanvas, self).save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (Pages 2+)
        if self._pageNumber > 1:
            self.drawString(54, letter[1] - 36, "Autonomous Financial Reconciliation Agent — Non-Technical Executive Guide")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, letter[1] - 42, letter[0] - 54, letter[1] - 42)

        # Footer (All pages)
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(letter[0] - 54, 30, page_str)
        self.drawString(54, 30, "Confidential & Educational • Capstone Project Prototype")
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 40, letter[0] - 54, 40)
        self.restoreState()

def build_pdf(filename: str):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'),
        spaceAfter=4
    )

    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor('#475569'),
        spaceAfter=12
    )

    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=17,
        textColor=colors.HexColor('#1E293B'),
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor('#0284C7'),
        spaceBefore=8,
        spaceAfter=3,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor('#334155'),
        spaceAfter=6
    )

    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.2,
        leading=13,
        textColor=colors.HexColor('#334155'),
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=4
    )

    callout_style = ParagraphStyle(
        'Callout_Text',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9.2,
        leading=13.5,
        textColor=colors.HexColor('#1E293B')
    )

    story = []

    # Title & Header
    story.append(Paragraph("Autonomous Financial Reconciliation Agent", title_style))
    story.append(Paragraph("A Plain-English, Non-Technical Guide for Evaluators, Judges & General Audiences", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0284C7"), spaceAfter=10))

    # Executive Elevator Pitch Box
    pitch_html = (
        "<b>The 30-Second Elevator Pitch:</b><br/>"
        "Most budgeting apps act like dumb sticker machines: they guess a label for your purchase (like 'Groceries'), "
        "and then stop. If they make a mistake, they secretly ruin your budget numbers without you ever knowing. "
        "Our project is an <b>intelligent digital accountant</b>. Instead of just guessing, it cleans up messy bank "
        "descriptions, checks your spending limits, sounds the alarm on fraud or double-charges, and if you're over-budget, "
        "<b>automatically figures out where to safely move money from</b> (like cutting your entertainment budget to cover dinner) "
        "while strictly protecting essential bills like rent and healthcare."
    )
    pitch_table = Table([[Paragraph(pitch_html, callout_style)]], colWidths=[letter[0]-108])
    pitch_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F0F9FF')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#BAE6FD')),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    story.append(pitch_table)
    story.append(Spacer(1, 10))

    # Section 1: The Problem
    story.append(Paragraph("1. The Real-World Problem: Why Current Banking Apps Fail You", h1_style))
    story.append(Paragraph(
        "Think about your banking app (like Mint, Rocket Money, or Chase). Whenever you swipe your card, the bank gets a messy, cryptic text description like <code>SQ *BLUE BOTTLE #12 SAN FRAN</code>. Existing apps usually try to classify this in one shot.",
        body_style
    ))
    story.append(Paragraph(
        "Here is why that is dangerous: <b>money is an interconnected web of envelopes.</b> If you spend $120 at a restaurant and the app wrongly tags it as 'Groceries':",
        body_style
    ))
    story.append(Paragraph("• <b>Damage 1:</b> Your 'Dining' budget looks like you spent nothing, hiding the fact that you overspent on luxury dinners.", bullet_style))
    story.append(Paragraph("• <b>Damage 2:</b> Your 'Groceries' budget now shows an artificial overspend, so the app warns you to stop buying food at home.", bullet_style))
    story.append(Paragraph("• <b>Damage 3:</b> If an automated system tries to give you advice, it will tell you to cut food when you should be cutting restaurant visits!", bullet_style))

    story.append(Spacer(1, 6))

    # Section 2: How It Works
    story.append(Paragraph("2. How Our Prototype Works: The 5 Steps of a Human Accountant", h1_style))
    story.append(Paragraph(
        "Rather than doing everything in a single blind guess, our AI behaves like a careful, trained human accountant. Every time a transaction arrives, it executes a 5-step checklist using dedicated mathematical tools:",
        body_style
    ))

    # Table of 5 steps
    steps_data = [
        [
            Paragraph("<b>Step & Tool</b>", h2_style),
            Paragraph("<b>What It Does (In Simple Terms)</b>", h2_style),
            Paragraph("<b>Everyday Analogy</b>", h2_style)
        ],
        [
            Paragraph("<b>1. Categorize Transaction</b>", body_style),
            Paragraph("Cleans up the gibberish bank code into a real merchant name (e.g. 'Blue Bottle Coffee') and picks the right category with a confidence score.", body_style),
            Paragraph("Reading a wrinkled, stamped receipt and writing 'Coffee' neatly at the top.", body_style)
        ],
        [
            Paragraph("<b>2. Check Budget Rule</b>", body_style),
            Paragraph("Looks up how much money is left in that specific envelope for the month and checks if you've hit 80% (Warning) or 100% (Exceeded).", body_style),
            Paragraph("Opening your 'Dining Out' cash envelope to count how many bills are left.", body_style)
        ],
        [
            Paragraph("<b>3. Flag Anomaly</b>", body_style),
            Paragraph("Compares the charge against your history to catch 3x price spikes, sudden duplicate charges within minutes, or shady crypto merchants.", body_style),
            Paragraph("A store clerk saying: <i>'Wait, you were charged twice within 3 minutes—let me check!'</i>", body_style)
        ],
        [
            Paragraph("<b>4. Calculate Variance</b>", body_style),
            Paragraph("Looks at every single budget category for the entire month to see the scoreboard: which envelopes have extra cash, and which are in debt.", body_style),
            Paragraph("Spreading all your household envelopes on the table to see total savings vs. holes.", body_style)
        ],
        [
            Paragraph("<b>5. Propose Reallocation</b>", body_style),
            Paragraph("Takes extra surplus money from fun/flexible pools (like Entertainment or Shopping) to fill the hole in Dining—<b>while strictly protecting rent and doctor bills.</b>", body_style),
            Paragraph("Borrowing $20 from your movie-night jar to cover dinner, without ever touching rent money.", body_style)
        ]
    ]

    steps_table = Table(steps_data, colWidths=[1.8*inch, 3.2*inch, 2.0*inch])
    steps_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor('#F8FAFC')),
        ('BACKGROUND', (0,3), (-1,3), colors.HexColor('#F8FAFC')),
        ('BACKGROUND', (0,5), (-1,5), colors.HexColor('#F8FAFC')),
    ]))
    story.append(steps_table)

    story.append(PageBreak())

    # Section 3: Why it is special (Gen AI + Tools)
    story.append(Paragraph("3. Why Is This Prototype Special Compared to Regular AI?", h1_style))
    story.append(Paragraph(
        "Many people think of AI as a chatbot like ChatGPT. But standard chatbots have a major flaw when it comes to finance: <b>they are notorious for hallucinating numbers.</b> If you ask ChatGPT to do complex budget math over hundreds of transactions, it will often make up totals that don't add up.",
        body_style
    ))
    story.append(Paragraph(
        "Our prototype solves this using a <b>Compound AI Architecture</b> (specifically, the ReAct paradigm):",
        body_style
    ))
    story.append(Paragraph("• <b>The AI is the 'Brain' (Planner):</b> It decides <i>what needs to be done next</i> (e.g. 'I categorized this dinner, now let me check if the dining budget has room').", bullet_style))
    story.append(Paragraph("• <b>The Tools are the 'Hands' (Calculators):</b> The AI never does the math itself. It sends the request to an un-bribeable, precise database that executes exact mathematical arithmetic.", bullet_style))
    story.append(Paragraph("• <b>Full Transparency (No Black Box):</b> Every single decision, tool call, and math calculation is recorded in an open, explainable <b>Executive Audit Trail</b> that anyone can inspect.", bullet_style))

    story.append(Spacer(1, 8))

    # Section 4: Key Discovery (The Domino Effect)
    story.append(Paragraph("4. Our Key Scientific Discovery: The 'Domino Effect' & Dollar Drift", h1_style))
    story.append(Paragraph(
        "In our research, we didn't just build the agent—we intentionally tried to break it to see what happens when AI goes wrong in banking. We injected a deliberate mistake into Step 1 (e.g. tagging dinner as groceries) across 11 complex scenarios.",
        body_style
    ))
    
    discovery_html = (
        "<b>The Shocking Result:</b><br/>"
        "• <b>100% Downstream Corruption:</b> Every subsequent tool blindly inherited the initial mistake.<br/>"
        "• <b>72.7% Compromised Advice:</b> In nearly three out of four cases, the final advice given to the user was completely invalid or backwards.<br/>"
        "• <b>The 'Dollar Drift' Multiplier ($2X):</b> If you miscategorize a $100 charge, you create a $100 hole in one envelope AND a $100 false surplus in another. That means a single $100 mistake creates <b>$200 in total budget distortion</b>! Across our test scenarios, early mistakes caused over <b>$6,353.08 in invisible financial distortion</b>."
    )
    disc_table = Table([[Paragraph(discovery_html, body_style)]], colWidths=[letter[0]-108])
    disc_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FEF2F2')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#FECACA')),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    story.append(disc_table)

    story.append(Spacer(1, 8))

    # Section 5: The Ready-to-Use 60-Second Script
    story.append(Paragraph("5. How to Explain This Project in 60 Seconds (Your Ready-to-Use Script)", h1_style))
    story.append(Paragraph(
        "If an interviewer, professor, or friend asks: <i>'What did you actually build?'</i>, here is the exact script to say:",
        body_style
    ))

    script_html = (
        "<i>\"Have you ever used a budgeting app that wrongly tagged a dinner receipt as 'groceries' and messed up all your charts? "
        "I built an autonomous AI financial reconciliation agent that fixes this problem. "
        "Instead of just slapping a single tag on a purchase, my agent acts like a live personal accountant: it parses the bank descriptor, "
        "checks your actual monthly budget limits, scans for fraud or double-billings, checks the overall financial health across all categories, "
        "and if you're in the red, it automatically moves spare money from fun categories like entertainment to balance your budget—without ever "
        "touching your rent or utility bills.<br/><br/>"
        "Best of all, I proved mathematically that when regular AI apps make a small classification mistake, the error doubles across your ledger "
        "— a phenomenon called 'Dollar Drift'. My prototype uses a locked 5-tool pipeline to eliminate AI math hallucinations and keep budgets balanced.\"</i>"
    )
    script_table = Table([[Paragraph(script_html, body_style)]], colWidths=[letter[0]-108])
    script_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    story.append(script_table)

    story.append(Spacer(1, 8))

    # Section 6: Quick FAQ
    story.append(Paragraph("6. Frequently Asked Non-Technical Questions", h1_style))
    story.append(Paragraph("<b>Q: Does the AI touch my real bank account?</b>", h2_style))
    story.append(Paragraph("A: No. In this prototype, it runs safely on an offline database loaded with realistic banking data. In a real-world version, it would require user approval before finalizing transfers.", body_style))

    story.append(Paragraph("<b>Q: What makes this better than Excel or a calculator?</b>", h2_style))
    story.append(Paragraph("A: Excel cannot read messy store names, understand English instructions, or autonomously decide how to rebalance competing budgets. Our agent combines the reading comprehension of modern AI with the mathematical certainty of a calculator.", body_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF successfully built at: {filename}")

if __name__ == "__main__":
    out_proj = "/Users/shaikmohammedafrazhussain/.gemini/antigravity-ide/scratch/financial-reconciliation-agent/non_technical_prototype_guide.pdf"
    out_art = "/Users/shaikmohammedafrazhussain/.gemini/antigravity-ide/brain/255a685a-4b23-4b66-b4d9-b2bbde53f572/non_technical_prototype_guide.pdf"
    
    build_pdf(out_proj)
    build_pdf(out_art)
