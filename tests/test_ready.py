"""The ready-to-send list: which questions a fit report still asks."""
from jobpipe.web.ready import parse_questions, question_key


def test_questions_come_from_the_reports_last_section():
    md = """# Fit report

## Suggested improvements
1. This is a rewrite, not a question to answer.

## Questions before I finalize
Ask these before the résumé is final.
1. The posting asks for hardware programs. Have you run one, and what was your role?
2. It asks for vendor management. Which vendors did you own?

## Scorecard stories
1. A story, not a question.
"""
    assert parse_questions(md) == [
        "The posting asks for hardware programs. Have you run one, and what was your role?",
        "It asks for vendor management. Which vendors did you own?",
    ]
    assert question_key(parse_questions(md)[0]) == question_key(
        "  the posting asks for hardware   programs. have you run one, and what was your role? ")


def test_a_report_without_that_heading_has_no_questions():
    assert parse_questions("# Report\n\n1. Not a follow-up.\n") == []
    assert parse_questions("## Questions before I finalize\n\nNothing numbered yet.\n- too short\n") == []
