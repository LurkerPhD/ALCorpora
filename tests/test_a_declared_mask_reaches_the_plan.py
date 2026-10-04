"""What a corpus declares about comparing two runs must reach the plan.

`drop`, `keep` and `mask` are the three shapes of "what not to compare", and
`mask` is the one a line needs when it mixes a JUDGED value with a
run-dependent one: dropping such a line stops comparing the judged value too,
which is a comparison that accepts a difference.

Measured while writing this: `keep` and `mask` reached the comparator
(`compare.normalize` takes them) but the DECLARATION could not carry them —
`_corpus_rules` read only `drop`/`rtol`/`atol` — so a corpus that declared a
mask had it silently dropped and its run compared the whole line. The two
halves have to be pinned together, because each half alone looks correct.
"""

from __future__ import annotations

import json
from pathlib import Path

from protocolize.onboard.extract import _corpus_rules, _lockstep_rules


def _decl(tmp_path: Path, comparison: dict) -> Path:
    p = tmp_path / 'declaration.json'
    p.write_text(json.dumps({'comparison': comparison}), encoding='utf-8')
    return p


def test_all_three_modes_travel_from_the_declaration(tmp_path: Path):
    decl = _decl(tmp_path, {
        'drop': [r'^\s*DATE:'],
        'keep': [r'RESULT'],
        'mask': [[r'IDBBAS\s*=\s*\S+', 'IDBBAS=<addr>']],
        'rtol': 0.1,
    })
    rules = _corpus_rules(decl)
    assert rules['drop'] == [r'^\s*DATE:']
    assert rules['keep'] == [r'RESULT']
    assert rules['mask'] == [[r'IDBBAS\s*=\s*\S+', 'IDBBAS=<addr>']]

    # ... and from the rules into an assertion's parameters.
    params = _lockstep_rules(rules)
    assert params['keep'] == [r'RESULT']
    assert params['mask'] == [[r'IDBBAS\s*=\s*\S+', 'IDBBAS=<addr>']]
    assert params['rtol'] == 0.1


def test_an_undeclared_mode_stays_absent(tmp_path: Path):
    """An empty list is noise; an absent key is the truth.

    The plan schema refuses a key the mechanism does not read, so a mode the
    corpus never declared must not be materialised as `[]` — that is a
    declaration the corpus did not make.
    """
    rules = _corpus_rules(_decl(tmp_path, {'drop': [r'^X']}))
    assert 'mask' not in rules and 'keep' not in rules
    params = _lockstep_rules(rules)
    assert 'mask' not in params and 'keep' not in params


def test_a_malformed_mask_pair_is_ignored_not_half_applied(tmp_path: Path):
    """A pair that is not `(pattern, replacement)` is not a rule."""
    rules = _corpus_rules(_decl(tmp_path, {
        'mask': [['only-one'], 'not-a-pair', [r'a', 'b']],
    }))
    assert rules['mask'] == [[r'a', 'b']], (
        'only well-formed pairs travel; a half-formed one is dropped rather '
        'than applied with a guessed replacement')
