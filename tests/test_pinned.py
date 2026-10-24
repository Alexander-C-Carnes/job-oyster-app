from jobpipe import config, pinned, render

RESUME = """# Pat Doe
**Program Manager | 10 Years**
Springfield, IL | linkedin.com/in/pat

## SUMMARY
Runs programs.

## PROFESSIONAL EXPERIENCE

**Contoso** | Program Manager
*Jan 2020–Present | Springfield*

- Ran the launch.

## ADDITIONAL EXPERIENCE

**Fabrikam** | Analyst
*Jan 2015–Dec 2019*

Wrote reports.

## CORE SKILLS
Hard Skills: a | b
"""

BLOCK = """## FOUNDER EXPERIENCE

**Side Co** | Founder
*Present*

- Built and launched Side Co."""


def test_apply_puts_the_block_after_professional_experience():
    md = pinned.apply(RESUME, BLOCK)
    assert md.index("## PROFESSIONAL EXPERIENCE") < md.index(BLOCK) < md.index("## ADDITIONAL EXPERIENCE")
    assert "- Ran the launch.\n\n## FOUNDER EXPERIENCE" in md
    assert BLOCK + "\n\n## ADDITIONAL EXPERIENCE" in md


def test_apply_replaces_a_copy_an_agent_rewrote():
    rewritten = pinned.apply(RESUME, BLOCK).replace("Built and launched Side Co.", "Founded Side Co.")
    md = pinned.apply(rewritten, BLOCK)
    assert md.count("## FOUNDER EXPERIENCE") == 1 and "Founded" not in md and BLOCK in md
    assert pinned.strip(md, BLOCK) == RESUME


def test_rows_without_a_heading_join_professional_experience():
    rows = BLOCK.split("\n", 2)[2]
    md = pinned.apply(RESUME, rows)
    assert "- Ran the launch.\n\n" + rows + "\n\n## ADDITIONAL EXPERIENCE" in md
    assert pinned.strip(md, rows) == RESUME


def test_no_block_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REFERENCES", tmp_path)
    assert pinned.text() == "" and pinned.note() == ""
    assert pinned.apply(RESUME) == RESUME == pinned.strip(RESUME)
    (tmp_path / "pinned.md").write_text(BLOCK + "\n")
    assert "word for word" in pinned.note() and BLOCK in pinned.note()
    assert BLOCK in pinned.apply(RESUME)


def test_the_renderer_draws_its_rows_as_jobs():
    assert render.role_section("FOUNDER EXPERIENCE") and not render.role_section("CORE SKILLS")
    assert '<span class="co">Side Co</span>' in render.render(pinned.apply(RESUME, BLOCK))


def test_render_marks_the_lines_asked_for():
    md = pinned.apply(RESUME, BLOCK)
    start = md[:md.index(BLOCK)].count("\n")
    page = render.render(md, mark=set(range(start, start + BLOCK.count("\n") + 1)))
    assert '<h2 class="pin">FOUNDER EXPERIENCE</h2>' in page and '<mark class="pin">Side Co</mark>' in page
    assert '<mark class="pin">Ran the launch.</mark>' not in page and "mark.pin" in page
    assert "pin" not in render.render(md)
