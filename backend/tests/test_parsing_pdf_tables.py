from types import SimpleNamespace

from fpdf import FPDF

from app.extraction.batching import build_batches, render
from app.parsing.flatten import flatten
from app.parsing.pdf_parser import PdfParser


def make_table(path, *, merged=False, side_note=False, pages=1, shaded=False):
    pdf = FPDF()
    for _ in range(pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        pdf.text(15, 20, "1 Evidence requirements")
        pdf.text(15, 28, "All teams must retain the following evidence.")
        if shaded:
            pdf.set_fill_color(50, 100, 180)
            pdf.rect(15, 40, 150, 15, style="F")
            pdf.rect(70, 40, 40, 7, style="F")
            pdf.rect(70, 47, 40, 8, style="F")
        # 合成网格：三列三行；首列可跨两行，首行可跨两列。
        for y in (40, 55, 70, 85):
            pdf.line(15 if y != 70 or not merged else 65, y, 165, y)
        for x in (15, 65, 115, 165):
            pdf.line(x, 55 if x == 115 and merged else 40, x, 85)
        values = [(20, 49, "Role"), (70, 49, "Evidence"), (20, 64, "Owner"),
                  (70, 64, "APPROVAL"), (120, 64, "Annual"),
                  (70, 79, "REVIEW"), (120, 79, "Monthly")]
        if not merged:
            values.extend([(120, 49, "Frequency"), (20, 79, "Reviewer")])
        for x, y, value in values:
            pdf.text(x, y, value)
        if side_note:
            pdf.text(170, 64, "NOTE")
        pdf.text(15, 98, "Exceptions must be approved.")
    pdf.output(path)
    return path


def test_table_relationships_reach_extraction_and_keep_source_cells(tmp_path):
    parsed = PdfParser().parse(make_table(tmp_path / "grid.pdf"))
    rows = flatten(parsed.clauses, document_id=1)
    tables = [r for r in rows if r['kind'] == 'table']
    assert len(tables) == 1
    table = tables[0]
    assert table['number'] is None
    assert '[column 1] Owner\n[column 2] APPROVAL\n[column 3] Annual' in table['text']
    assert '[column 1] Reviewer\n[column 2] REVIEW\n[column 3] Monthly' in table['text']
    cells = [b for b in parsed.source_blocks if b.kind == 'table_cell']
    assert len(cells) == 9
    assert next(c for c in cells if c.text == 'Annual').locator['positions'] == [{'row': 2, 'column': 3}]
    assert all(c.locator['page'] == 1 and len(c.locator['bbox']) == 4 for c in cells)
    assert {b.key for b in parsed.source_blocks} == {k for r in rows for k in r['_source_keys']}
    parent = rows[table['_parent_key']]
    assert 'APPROVAL' not in parent['text']
    assert 'Exceptions must be approved.' in parent['text']
    clauses = [SimpleNamespace(**{k: v for k, v in r.items() if not k.startswith('_')}, id=i+1,
                      parent_id=r['_parent_key']+1 if r['_parent_key'] is not None else None)
               for i, r in enumerate(rows)]
    batch = next(b for b in build_batches(clauses) if any(c.kind == 'table' for c in b.clauses))
    prompt = render(batch)
    assert table['text'] in prompt
    assert 'All teams must retain' in prompt and 'Exceptions must be approved.' in prompt


def test_merged_cells_have_one_source_and_explicit_span(tmp_path):
    parsed = PdfParser().parse(make_table(tmp_path / 'merged.pdf', merged=True))
    cells = [b for b in parsed.source_blocks if b.kind == 'table_cell']
    assert len(cells) == 7
    owner = next(c for c in cells if c.text == 'Owner')
    assert owner.locator['positions'] == [{'row': 2, 'column': 1}, {'row': 3, 'column': 1}]
    evidence = next(c for c in cells if c.text == 'Evidence')
    assert evidence.locator['positions'] == [{'row': 1, 'column': 2}, {'row': 1, 'column': 3}]
    table = next(r for r in flatten(parsed.clauses, document_id=1) if r['kind'] == 'table')
    assert table['text'].count('Owner') == 1
    assert '[column 1; spans rows 2-3] Owner' in table['text']
    assert '[column 2-3] Evidence' in table['text']


def test_text_crossing_table_boundary_falls_back_without_loss(tmp_path):
    parsed = PdfParser().parse(make_table(tmp_path / 'side-note.pdf', side_note=True))
    rows = flatten(parsed.clauses, document_id=1)
    assert not any(r['kind'] == 'table' for r in rows)
    assert any('NOTE' in r['text'] or 'NOTE' in r['heading'] for r in rows)
    assert {b.key for b in parsed.source_blocks} == {k for r in rows for k in r['_source_keys']}


def test_page_tables_keep_unique_cells_and_do_not_merge_repeated_headers(tmp_path):
    parsed = PdfParser().parse(make_table(tmp_path / 'pages.pdf', pages=2))
    rows = flatten(parsed.clauses, document_id=1)
    tables = [r for r in rows if r['kind'] == 'table']
    assert [t['page_ref'] for t in tables] == [1, 2]
    cells = [b for b in parsed.source_blocks if b.kind == 'table_cell']
    assert len({c.key for c in cells}) == len(cells) == 18
    assert {c.locator['table'] for c in cells} == {1, 2}


def test_filled_header_background_does_not_invent_columns_or_rows(tmp_path):
    parsed = PdfParser().parse(make_table(tmp_path / "shaded.pdf", shaded=True))
    cells = [b for b in parsed.source_blocks if b.kind == "table_cell"]
    assert len(cells) == 9
    assert max(p["column"] for c in cells for p in c.locator["positions"]) == 3
    assert max(p["row"] for c in cells for p in c.locator["positions"]) == 3


async def test_table_cell_provenance_survives_database_round_trip(db_session, tmp_path):
    from sqlalchemy import select

    from app.clauses.models import Clause
    from app.clauses.sources import source_page
    from app.ingest.models import DocType, Document
    from app.parsing.flatten import persist

    parsed = PdfParser().parse(make_table(tmp_path / "persist.pdf", merged=True))
    doc = Document(title="Grid", doc_type=DocType.PROCEDURE, file_hash="a" * 64,
                   file_path="/persist.pdf", original_filename="persist.pdf")
    db_session.add(doc)
    await db_session.flush()
    for _ in range(2):
        await persist(db_session, parsed.clauses, document_id=doc.id,
                      source_blocks=parsed.source_blocks, source_origin=parsed.source_origin)
    table = await db_session.scalar(select(Clause).where(Clause.document_id == doc.id, Clause.kind == "table"))
    sources = await source_page(db_session, doc.id, clause_id=table.id)
    assert sources["unassigned"] == 0
    cells = [b for b in sources["blocks"] if b["kind"] == "table_cell"]
    assert len(cells) == 7
    owner = next(c for c in cells if c["text"] == "Owner")
    assert owner["locator"]["positions"] == [{"row": 2, "column": 1}, {"row": 3, "column": 1}]
    assert owner["locator"]["page"] == 1
    assert owner["clause_ids"] == [table.id]


def make_continuation(path, *, changed_header=False, interruption=False):
    pdf = FPDF()
    for page in range(2):
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        top, bottom = (40, 280) if page == 0 else (20, 65)
        if page == 0:
            pdf.text(15, 20, "Responsibilities matrix")
        elif interruption:
            pdf.text(15, 12, "Separate requirements")
        for y in (top, top + 10, bottom):
            pdf.line(15, y, 165, y)
        for x in (15, 65, 115, 165):
            pdf.line(x, top, x, bottom)
        for x, value in [(20, "Role"), (70, "Evidence"),
                         (120, "Deadline" if changed_header and page else "Frequency")]:
            pdf.text(x, top + 7, value)
        for x, value in [(20, "Owner" if page == 0 else "Reviewer"),
                         (70, "Approval" if page == 0 else "Review"), (120, "Annual")]:
            pdf.text(x, top + 18, value)
        if page:
            pdf.text(15, 80, "Exceptions require written approval.")
    pdf.output(path)
    return path


def test_continuation_keeps_both_pages_and_following_context(tmp_path):
    parsed = PdfParser().parse(make_continuation(tmp_path / "continued.pdf"))
    rows = flatten(parsed.clauses, document_id=1)
    tables = [r for r in rows if r["kind"] == "table"]
    assert len(tables) == 1
    assert "pages 1-2" in tables[0]["heading"]
    assert "Continuation — page 2" in tables[0]["text"]
    assert "Owner" in tables[0]["text"] and "Reviewer" in tables[0]["text"]
    assert "Exceptions require written approval." in tables[0]["text"]
    cells = [b for b in parsed.source_blocks if b.kind == "table_cell"]
    assert len(cells) == 12
    assert all(c.locator["continues_table"] == 1 for c in cells if c.locator["page"] == 2)
    assert {b.key for b in parsed.source_blocks} == {k for r in rows for k in r["_source_keys"]}


def test_changed_header_and_intervening_caption_prevent_merge(tmp_path):
    for options in ({"changed_header": True}, {"interruption": True}):
        parsed = PdfParser().parse(make_continuation(tmp_path / "separate.pdf", **options))
        rows = flatten(parsed.clauses, document_id=1)
        assert len([r for r in rows if r["kind"] == "table"]) == 2
        assert not any("continues_table" in b.locator for b in parsed.source_blocks)
