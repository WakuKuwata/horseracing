"""JRA official-site parsers (Feature 176). Input: decoded ``str``; output: ``jra.models`` objects.

Every parser is fail-closed (FR-008): a missing required element raises ``ParseError`` for that
page or marks that race block ``parse_failed`` — partial values are never returned.
"""
