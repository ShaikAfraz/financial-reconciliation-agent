"""
Script to generate a publication-grade, professionally formatted Word document (.docx)
for the 2-page Extended Abstract of the Autonomous Financial Reconciliation Agent.
"""

import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from pathlib import Path

def set_cell_background(cell, hex_color):
    """Sets the background color of a table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    tc_pr.append(shd)

def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    """Sets inner margins/padding of a table cell in dxa (1 pt = 20 dxa)."""
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = parse_xml(f'<w:tcMar {nsdecls("w")}><w:top w:w="{top}" w:type="dxa"/><w:bottom w:w="{bottom}" w:type="dxa"/><w:left w:w="{left}" w:type="dxa"/><w:right w:w="{right}" w:type="dxa"/></w:tcMar>')
    tc_pr.append(tc_mar)

def create_abstract_docx(output_path: str):
    doc = Document()

    # Set page margins: 0.75 inch margins for clean 2-page technical extended abstract
    for section in doc.sections:
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

    # Style presets
    normal_style = doc.styles['Normal']
    normal_style.font.name = 'Calibri'
    normal_style.font.size = Pt(10)
    normal_style.font.color.rgb = RGBColor(30, 41, 59) # Slate 800

    # 1. Document Title
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(2)
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_title = p_title.add_run("Autonomous Financial Reconciliation Agent")
    run_title.font.name = 'Calibri'
    run_title.font.size = Pt(17)
    run_title.font.bold = True
    run_title.font.color.rgb = RGBColor(15, 23, 42)

    # Subtitle
    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_before = Pt(0)
    p_sub.paragraph_format.space_after = Pt(4)
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_sub = p_sub.add_run("Multi-Step Transaction Categorization, Anomaly Detection, and Budget Reallocation Planning via Tool-Calling LLM")
    run_sub.font.name = 'Calibri'
    run_sub.font.size = Pt(10.5)
    run_sub.font.bold = True
    run_sub.font.color.rgb = RGBColor(51, 65, 85)

    p_meta = doc.add_paragraph()
    p_meta.paragraph_format.space_before = Pt(0)
    p_meta.paragraph_format.space_after = Pt(12)
    p_meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_meta = p_meta.add_run("Extended Technical Abstract & Experimental Evaluation Report")
    run_meta.font.size = Pt(9.5)
    run_meta.font.italic = True
    run_meta.font.color.rgb = RGBColor(100, 116, 139)

    # 2. Executive Abstract Box
    tbl_abs = doc.add_table(rows=1, cols=1)
    tbl_abs.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl_abs.autofit = False
    tbl_abs.columns[0].width = Inches(7.0)
    cell_abs = tbl_abs.rows[0].cells[0]
    set_cell_background(cell_abs, "F1F5F9") # Slate 100
    set_cell_margins(cell_abs, top=140, bottom=140, left=180, right=180)

    p_abs = cell_abs.paragraphs[0]
    p_abs.paragraph_format.space_before = Pt(0)
    p_abs.paragraph_format.space_after = Pt(0)
    r_abs_bold = p_abs.add_run("Abstract—")
    r_abs_bold.font.bold = True
    r_abs_bold.font.size = Pt(9.5)
    r_abs_text = p_abs.add_run(
        "Contemporary personal finance management (PFM) applications evaluate transactions as isolated, single-turn classifications. Consequently, early categorization errors silently corrupt downstream budget envelopes, skew historical spending baselines, and yield generic reallocation advice disconnected from empirical financial variance. This paper presents an autonomous agent prototype that models financial reconciliation as an end-to-end, multi-turn reasoning chain. Utilizing a Compound AI System governed by the ReAct (Reasoning + Acting) tool-calling paradigm, the agent orchestrates a fixed 5-tool pipeline—parsing raw banking statement descriptors, auditing budget ceilings, flagging statistical anomalies, computing category-level budget variances, and generating mathematically balanced fund reallocations. Our primary contribution is a dual-layer evaluation framework: (1) a Ground-Truth Plan-Sequence Benchmark of 18 scenarios measuring sequence validity; and (2) an Injected-Perturbation Failure-Mode Analysis. Findings reveal that while the agent achieves 100.0% Plan-Sequence Correctness and 94.9% Recommendation Groundedness under nominal conditions, an unmitigated Step 1 misclassification cascades into 100.0% of downstream tool calls, corrupts 72.7% of final recommendations, and introduces $6,353.08 in cumulative ledger Dollar Drift ($) across 11 multi-step scenarios."
    )
    r_abs_text.font.size = Pt(9.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # Section Helper
    def add_section_header(title):
        h = doc.add_paragraph()
        h.paragraph_format.space_before = Pt(8)
        h.paragraph_format.space_after = Pt(3)
        h.paragraph_format.keep_with_next = True
        r = h.add_run(title)
        r.font.name = 'Calibri'
        r.font.size = Pt(11.5)
        r.font.bold = True
        r.font.color.rgb = RGBColor(15, 23, 42)
        return h

    # Section 1
    add_section_header("1. Problem Statement & Research Motivation")
    p1 = doc.add_paragraph()
    p1.paragraph_format.space_after = Pt(4)
    p1.add_run(
        "Traditional financial management software treats transaction categorization as an isolated classification task. When a $120.00 charge at a restaurant is erroneously categorized under 'Groceries' instead of 'Dining', two structural distortions occur: (1) Dining under-reports an undetected deficit; and (2) Groceries prematurely exhausts food allowances. In automated decision systems, this mistake compounds downstream, prompting agents to cut grocery budgets while luxury dining continues unchecked. While tool-calling agents are increasingly prevalent, measuring whether an agent's multi-step sequence holds together across successive dependent tools—and quantifying the exact error cascade—remains under-researched. This prototype establishes an empirical framework to address this gap."
    )

    # Section 2
    add_section_header("2. Compound AI System Architecture & The 5-Tool Pipeline")
    p2 = doc.add_paragraph()
    p2.paragraph_format.space_after = Pt(4)
    p2.add_run(
        "The prototype is engineered as a local-first Compound AI System operating over an embedded SQLite ledger (reconciliation.db) seeded with realistic Kaggle-aligned personal finance records. The agent dispatches structured function calls against a locked catalog of 5 deterministic tools:"
    )

    tools_bullets = [
        ("Tool 1: categorize_transaction", "Normalizes messy bank descriptors (e.g. 'SQ *BLUE BOTTLE', 'TST* SWEETGREEN') by stripping clearing prefixes and POS identifiers, calculating token-similarity scores with explicit uncertainty thresholds (< 60% flags ambiguity)."),
        ("Tool 2: check_against_budget_rule", "Executes SQL aggregations to evaluate cumulative category spend against defined monthly limits, classifying status into Healthy (OK), Warning (≥ 80%), or Exceeded (> 100%)."),
        ("Tool 3: flag_anomaly", "Evaluates transactions against empirical database baselines (detecting 3.0x amount spikes, rapid duplicate billings within 600s, and offshore crypto merchant signatures)."),
        ("Tool 4: calculate_category_variance", "Computes monthly budget versus actual spend across all ledger envelopes using SQL joins, isolating deficit overages and surplus headroom."),
        ("Tool 5: propose_reallocation", "Generates priority-weighted budget rebalancing from flexible discretionary pools (Entertainment: 1.0, Dining: 0.9, Shopping: 0.8) while strictly protecting non-discretionary necessities (Housing: 0.0, Healthcare: 0.1, Utilities: 0.1).")
    ]

    for t_name, t_desc in tools_bullets:
        bp = doc.add_paragraph(style='List Bullet')
        bp.paragraph_format.space_before = Pt(0)
        bp.paragraph_format.space_after = Pt(2)
        r_name = bp.add_run(f"{t_name}: ")
        r_name.font.bold = True
        r_name.font.size = Pt(9.5)
        r_desc = bp.add_run(t_desc)
        r_desc.font.size = Pt(9.5)

    # Section 3
    add_section_header("3. Ground-Truth Plan-Sequence Benchmark (18 Scenarios)")
    p3 = doc.add_paragraph()
    p3.paragraph_format.space_after = Pt(4)
    p3.add_run(
        "To evaluate whether the agent executes tools in a valid causal sequence, we hand-constructed 18 diverse scenarios with manually verified ground-truth sequences. Three core metrics were measured across the benchmark:"
    )

    # Table 1: Benchmark Metrics
    tbl_bench = doc.add_table(rows=4, cols=4)
    tbl_bench.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl_bench.autofit = False
    col_widths = [Inches(2.5), Inches(1.4), Inches(1.5), Inches(1.6)]
    for row in tbl_bench.rows:
        for idx, width in enumerate(col_widths):
            row.cells[idx].width = width

    headers_bench = ["Metric", "PRD Target", "Measured Score", "Benchmark Status"]
    for idx, h in enumerate(headers_bench):
        cell = tbl_bench.rows[0].cells[idx]
        set_cell_background(cell, "1E293B")
        set_cell_margins(cell, top=80, bottom=80, left=100, right=100)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r = p.add_run(h)
        r.font.bold = True
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(255, 255, 255)

    data_bench = [
        ("Tool-Selection Accuracy", "≥ 85.0%", "100.0%", "PASSED"),
        ("Plan-Sequence Correctness (Headline)", "≥ 70.0%", "100.0%", "PASSED"),
        ("Recommendation Groundedness", "≥ 90.0%", "94.9%", "PASSED")
    ]

    for row_idx, data in enumerate(data_bench, start=1):
        for col_idx, text in enumerate(data):
            cell = tbl_bench.rows[row_idx].cells[col_idx]
            if row_idx % 2 == 1:
                set_cell_background(cell, "F8FAFC")
            set_cell_margins(cell, top=60, bottom=60, left=100, right=100)
            p = cell.paragraphs[0]
            r = p.add_run(text)
            r.font.size = Pt(9)
            if col_idx == 0:
                r.font.bold = True
            elif col_idx == 2:
                r.font.bold = True
                r.font.color.rgb = RGBColor(16, 185, 129) # Green
            elif col_idx == 3:
                r.font.bold = True
                r.font.color.rgb = RGBColor(30, 41, 59)

    doc.add_paragraph().paragraph_format.space_after = Pt(2)

    # Section 4
    add_section_header("4. Failure-Mode Analysis: Quantifying Error Cascades & Dollar Drift ($)")
    p4 = doc.add_paragraph()
    p4.paragraph_format.space_after = Pt(4)
    p4.add_run(
        "The core scientific finding evaluates the compound fragility of agentic pipelines. Using a paired perturbation suite, Step 1 misclassifications were injected across 11 multi-step scenarios to monitor downstream corruption. We formalize Ledger Dollar Drift ($) as the sum of bidirectional envelope distortions: Drift(X) = 2X. If $120.00 of Dining is mislabeled as Groceries, Dining is under-reported by $120.00 and Groceries is over-reported by $120.00, generating $240.00 in total ledger distortion."
    )

    # Table 2: Error Propagation Results
    tbl_err = doc.add_table(rows=4, cols=3)
    tbl_err.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl_err.autofit = False
    err_col_widths = [Inches(3.2), Inches(1.8), Inches(2.0)]
    for row in tbl_err.rows:
        for idx, width in enumerate(err_col_widths):
            row.cells[idx].width = width

    headers_err = ["Failure-Mode Metric", "Empirical Value", "System Implication"]
    for idx, h in enumerate(headers_err):
        cell = tbl_err.rows[0].cells[idx]
        set_cell_background(cell, "1E293B")
        set_cell_margins(cell, top=80, bottom=80, left=100, right=100)
        p = cell.paragraphs[0]
        r = p.add_run(h)
        r.font.bold = True
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(255, 255, 255)

    data_err = [
        ("Downstream Step Corruption Rate", "100.0% (21/21 steps)", "All subsequent tools blindly inherit bad categories"),
        ("Cumulative Ledger Dollar Drift", "$6,353.08", "Substantial financial distortion across budget pools"),
        ("Final Recommendation Corruption Rate", "72.7% (8/11 scenarios)", "Almost 3/4 of final advice recommended invalid actions")
    ]

    for row_idx, data in enumerate(data_err, start=1):
        for col_idx, text in enumerate(data):
            cell = tbl_err.rows[row_idx].cells[col_idx]
            if row_idx % 2 == 1:
                set_cell_background(cell, "F8FAFC")
            set_cell_margins(cell, top=60, bottom=60, left=100, right=100)
            p = cell.paragraphs[0]
            r = p.add_run(text)
            r.font.size = Pt(9)
            if col_idx == 0:
                r.font.bold = True
            elif col_idx == 1:
                r.font.bold = True
                if "$" in text or "100" in text:
                    r.font.color.rgb = RGBColor(220, 38, 38) # Red

    doc.add_paragraph().paragraph_format.space_after = Pt(2)

    # Section 5 & 6
    add_section_header("5. Prototype Implementation & Production Roadmap")
    p5 = doc.add_paragraph()
    p5.paragraph_format.space_after = Pt(4)
    p5.add_run(
        "The deliverable includes: (1) a unified CLI (cli.py) with ANSI trace streaming; (2) a high-density, professional executive web dashboard (web/) displaying clean audit cards without raw code bloat; and (3) a dual-mode engine supporting offline deterministic execution alongside live Google Gemini (gemini-2.5-flash). "
        "The production roadmap targets: (a) real-time Plaid/Open Banking webhooks; (b) persistent vector memory (pgvector) indexing historical vendor patterns; and (c) human-in-the-loop authorization gates before ledger reallocations are finalized."
    )

    # Section 7
    add_section_header("6. Conclusion")
    p6 = doc.add_paragraph()
    p6.paragraph_format.space_after = Pt(0)
    p6.add_run(
        "By formalizing a 5-tool ReAct agent pipeline, verifying an 18-scenario plan-sequence benchmark, and mathematically quantifying Ledger Dollar Drift, this prototype demonstrates that multi-step financial reconciliation requires continuous causal validation rather than isolated classification. The entire codebase is verified (10/10 unit tests passing) and reproducible from a fresh clone with zero mandatory external dependencies."
    )

    doc.save(output_path)
    print(f"Document successfully created at: {output_path}")

if __name__ == "__main__":
    proj_out = "/Users/shaikmohammedafrazhussain/.gemini/antigravity-ide/scratch/financial-reconciliation-agent/abstract_summary.docx"
    artifact_out = "/Users/shaikmohammedafrazhussain/.gemini/antigravity-ide/brain/255a685a-4b23-4b66-b4d9-b2bbde53f572/abstract_summary.docx"
    
    create_abstract_docx(proj_out)
    create_abstract_docx(artifact_out)
