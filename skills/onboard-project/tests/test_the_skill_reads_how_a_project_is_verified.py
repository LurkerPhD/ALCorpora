"""The skill reads a project's acceptance the way it reads its build.

The onboarding skill's first principle is "the build is the authority on the
build" — ask the build system's own interfaces, never parse its files.  The
acceptance half has the same principle and it was not being applied: a
corpus's cases, its criteria and its tolerances are things the PROJECT
declares, and the skill must read those declarations rather than let three
Python scripts hardcode a guess at them.

Three corpora declare it three ways, and the ladder below is the inference —
from what the project HAS, not from its name:

* a built tree whose build system registered tests (LAPACK: 215 CTest
  registrations, already captured into ``Protocol.tests``);
* a case list with an INCLUDE chain and per-record fields (MYSTRAN's
  ``cases.txt``: ``type; deck; selector; operation; reference; tolerance``);
* one directory per case with a fixed-named input inside (NASTRAN's
  ``verification/<NAME>/<stem>.inp``).

Each fixture below is a tree in one of those shapes.  Nothing in the capture
code names a corpus; what it keys on is the shape of the declaration.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from alcorpora.onboard.extract import capture_verification
from alcorpora.private_protocol import Product, Protocol

# ── the three declaration shapes, as fixtures ─────────────────

def _registered_tree(tmp: Path) -> Path:
    """A tree whose build system registered its tests."""
    tree = tmp / 'build'
    (tree / 'bin').mkdir(parents=True)
    (tree / 'liblapack.a').write_bytes(b'')
    return tree


def _case_list_tree(tmp: Path) -> Path:
    """A checkout declaring cases as records in a file, with INCLUDEs."""
    proj = tmp / 'proj'
    (proj / 'decks').mkdir(parents=True)
    (proj / 'decks' / 'bar_01.DAT').write_text('deck\n')
    (proj / 'decks' / 'bar_02.DAT').write_text('deck\n')
    (proj / 'cases_new.txt').write_text(
        '# a comment\n'
        'mys;    decks/bar_01.DAT;   ;  \n'
        'mys;    decks/bar_02.DAT;   SC/1/DISPLACEMENTS/GID/900/TX;;  0.1394;  1e-3%;\n')
    (proj / 'cases.txt').write_text(
        '# Put new cases here.\n'
        'INCLUDE cases_new.txt\n')
    return proj


def _one_per_dir_tree(tmp: Path) -> Path:
    """A checkout declaring one case per directory."""
    proj = tmp / 'proj'
    for name in ('D01-00-0A', 'D02-01-1A'):
        d = proj / 'verification' / name
        d.mkdir(parents=True)
        (d / f'{name.lower().replace("-", "")}.inp').write_text('INP\n')
    (proj / 'verification' / 'D02-01-1A' / 'rscards').write_text('SUP\n')
    return proj


def _proto(tests: list | None = None) -> Protocol:
    return Protocol(
        compilation=[], generated_inputs=[],
        products=[Product(output='${TREE}/bin/prog', kind='exe',
                          stems=('a',))],
        tests=tests or [], comment='t')


def _tree(tmp: Path) -> Path:
    tree = tmp / 'build'
    tree.mkdir(parents=True, exist_ok=True)
    return tree


# ── 1. the build system's own registry ────────────────────────

def test_a_registered_suite_is_the_most_explicit_declaration(tmp_path):
    """What the build system registered outranks anything else found.

    It is the project's own statement, taken through the interface the build
    system maintains for the question — the same reason `capture_tests` asks
    `ctest --show-only=json-v1` rather than parsing a Testfile.
    """
    tree, proj = _registered_tree(tmp_path), _case_list_tree(tmp_path)
    proto = _proto(tests=[{'name': 'LAPACK_Test_Summary', 'command': ['x']},
                          {'name': 'BLAS-xblat1c', 'command': ['y']}])

    v = capture_verification(proto, tree, tree, proj)

    assert v['cases']['kind'] == 'build_system_tests'
    assert v['target']['kind'] == 'tree'
    assert v['run']['kind'] == 'suite'


def test_a_registered_suite_carries_NO_golden(tmp_path):
    """A self-verifying corpus: its drivers compute their own residuals."""
    tree = _registered_tree(tmp_path)
    proto = _proto(tests=[{'name': 't', 'command': ['x']}])

    v = capture_verification(proto, tree, tree, None)

    assert v['golden']['source'] == 'none'
    assert v['golden']['stamp']


# ── 2. a case list, read as the project writes it ─────────────

def test_a_case_list_is_read_with_its_INCLUDE_chain(tmp_path):
    tree, proj = _tree(tmp_path), _case_list_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert v['cases']['kind'] == 'case_list'
    assert v['cases']['files'] == ['cases.txt']
    # INCLUDE is what makes the real list reachable; without following it the
    # capture sees a file of comments.
    assert v['cases']['include']['directive'] == 'INCLUDE'


def test_the_case_list_columns_are_the_PROJECTS_own_fields(tmp_path):
    """The record layout is data, so a corpus that gains a column is a plan
    change rather than a parser change."""
    tree, proj = _tree(tmp_path), _case_list_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    rec = v['cases']['record']
    assert rec['sep'] == ';'
    assert rec['types']['field'] == 0
    assert set(rec['types']['accept']) == {'mys', 'msc', 'pth'}
    assert rec['deck']['field'] == 1


def test_the_per_quantity_criteria_are_kept_not_dropped(tmp_path):
    """MYSTRAN declares ``selector ; operation ; reference ; tolerance`` per
    record, and NOTHING in this repo reads it today.

    It is the project's own fine-grained criterion, and the only external
    truth available for that corpus.
    """
    tree, proj = _tree(tmp_path), _case_list_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    rec = v['cases']['record']
    assert rec['selector']['field'] == 2
    assert rec['operation']['field'] == 3
    assert rec['expected']['field'] == 4
    assert rec['tolerance']['field'] == 5
    # One deck per case, not one per assertion record.
    assert rec['group_by'] == 'deck'


def test_a_case_list_corpus_judges_a_PRODUCT_not_a_tree(tmp_path):
    tree, proj = _tree(tmp_path), _case_list_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert v['target']['kind'] == 'product'
    assert v['run']['kind'] == 'process'
    assert v['inputs']['from'] == 'input_only'


# ── 3. one directory per case ─────────────────────────────────

def test_a_one_per_dir_declaration_is_detected_and_read(tmp_path):
    tree, proj = _tree(tmp_path), _one_per_dir_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert v['cases']['kind'] == 'one_per_dir'
    assert v['cases']['id']['from'] == 'dirname'
    # `{stem}` is the id in the corpus's own spelling; `{id}` is verbatim.
    assert v['cases']['input']['name'] in ('{id}.inp', '{stem}.inp')


def test_a_one_per_dir_corpus_stages_the_WHOLE_case_dir(tmp_path):
    """NASTRAN's cases carry support decks and restart state beside the input,
    so ``input_only`` would stage a case that cannot run.

    The excluded suffixes are the project's own derived artifacts, and they
    are declared rather than inferred.
    """
    tree, proj = _tree(tmp_path), _one_per_dir_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert v['inputs']['from'] == 'case_dir'
    assert set(v['inputs']['exclude_suffix']) == {'.out', '.sof1', '.txt'}


# ── the input naming rule, derived from the project's own tree ─

def _nastran_like_tree(tmp: Path) -> Path:
    """The shape that defeated the first two attempts: a directory per case
    holding its input BESIDE support files, plus a directory that is not a
    case, plus a case whose input is missing."""
    proj = tmp / 'proj'
    v = proj / 'verification'
    for name, extra in (('D01-00-0A', ()), ('D02-01-1A', ('rscards',))):
        d = v / name
        d.mkdir(parents=True)
        (d / f'{name.lower().replace("-", "")}.inp').write_text('INP\n')
        for e in extra:
            (d / e).write_text('support\n')
    (v / 'Archive').mkdir(parents=True)          # empty: not a case
    return proj


def test_the_input_spelling_is_DERIVED_from_the_projects_own_tree(tmp_path):
    """``D01-00-0A`` holds ``d01000a.inp`` — a normalisation no ``{id}``
    template can express, and no assumption can be trusted to guess."""
    tree, proj = _tree(tmp_path), _nastran_like_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert v['cases']['input']['name'] == '{stem}.inp'
    assert v['cases']['input']['id_spelling'] == {'lower': True, 'remove': '-'}


def test_a_support_file_does_not_make_a_case_look_like_a_non_case(tmp_path):
    """The directory holds its input BESIDE ``rscards``/``COSHYD1``/``.optp``.

    Choosing the file by name order marked 13 real cases of the real corpus
    as non-cases; the rule has to match ANY file in the directory.
    """
    tree, proj = _tree(tmp_path), _nastran_like_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert 'D02-01-1A' not in v['cases']['exclude']


def test_a_directory_the_rule_cannot_explain_is_not_a_case(tmp_path):
    """Including it would invent a case the corpus never declared, and it
    would then have no golden.  Measured: 133 discovered where the corpus's
    own discovery finds 132."""
    tree, proj = _tree(tmp_path), _nastran_like_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert v['cases']['exclude'] == ['Archive']


# ── what every plan must carry, whatever the shape ────────────

@pytest.mark.parametrize('shape', ['registered', 'case_list', 'one_per_dir'])
def test_every_captured_plan_is_EXECUTABLE(tmp_path, shape):
    """A captured plan goes straight to the runner, so it must be complete —
    and `problems()` is what decides that, not a hope."""
    from alcorpora.verification_plan import VerificationPlan
    tree = _tree(tmp_path)
    if shape == 'registered':
        proto, proj = _proto(tests=[{'name': 't', 'command': ['x']}]), None
    elif shape == 'case_list':
        proto, proj = _proto(), _case_list_tree(tmp_path)
    else:
        proto, proj = _proto(), _one_per_dir_tree(tmp_path)

    v = capture_verification(proto, tree, tree, proj)

    assert VerificationPlan.from_json(v).problems() == []


@pytest.mark.parametrize('shape', ['registered', 'case_list', 'one_per_dir'])
def test_every_captured_plan_requires_something_to_have_happened(tmp_path, shape):
    """V0, at the capture end: the skill may not emit a plan that a run whose
    results nobody could read would satisfy."""
    tree = _tree(tmp_path)
    if shape == 'registered':
        proto, proj = _proto(tests=[{'name': 't', 'command': ['x']}]), None
    elif shape == 'case_list':
        proto, proj = _proto(), _case_list_tree(tmp_path)
    else:
        proto, proj = _proto(), _one_per_dir_tree(tmp_path)

    v = capture_verification(proto, tree, tree, proj)

    assert v['summary']['require_min']
    assert all(n >= 1 for n in v['summary']['require_min'].values())


def test_a_project_with_no_acceptance_declaration_is_REFUSED(tmp_path):
    """Silence is not a plan.  A corpus whose cases the skill cannot find
    must be refused, not given an empty plan that passes vacuously."""
    tree = _tree(tmp_path)
    empty = tmp_path / 'nothing'

    with pytest.raises(ValueError, match='declar'):
        capture_verification(_proto(), tree, tree, empty)


# ── where the declaration LIVES ───────────────────────────────

def _sibling_validation_tree(tmp: Path) -> Path:
    """A project whose acceptance suite is maintained as a SIBLING.

    This is the normal arrangement when the suite has its own life: MYSTRAN's
    sources are under ``benchmarks/MYSTRAN`` and its validation decks under
    ``benchmarks/MYSTRAN_Validation``, fetched by a separate setup step.  A
    search rooted only at the project finds nothing, and the refusal then
    names a project that is in fact fully equipped.
    """
    (tmp / 'proj' / 'src').mkdir(parents=True)
    val = tmp / 'proj_Validation'
    (val / 'decks').mkdir(parents=True)
    (val / 'decks' / 'a.DAT').write_text('deck\n')
    (val / 'cases.txt').write_text('mys; decks/a.DAT;\n')
    return tmp / 'proj'


def test_acceptance_material_beside_the_project_is_found(tmp_path):
    """The search reaches a sibling, because that is where it often is."""
    proj = _sibling_validation_tree(tmp_path)
    tree = _tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert v['cases']['kind'] == 'case_list'
    assert 'proj_Validation' in v['cases']['root']


def test_two_candidate_declarations_are_REFUSED_not_guessed(tmp_path):
    """Ambiguity is named, never resolved by picking one.

    Picking silently is how a corpus comes to be verified against another
    project's cases — and the failure looks like a wrong answer rather than a
    wrong choice.
    """
    proj = _sibling_validation_tree(tmp_path)
    other = tmp_path / 'proj_validation'
    (other / 'decks').mkdir(parents=True)
    (other / 'decks' / 'b.DAT').write_text('deck\n')
    (other / 'cases.txt').write_text('mys; decks/b.DAT;\n')
    tree = _tree(tmp_path)

    with pytest.raises(ValueError, match='[Aa]mbiguous'):
        capture_verification(_proto(), tree, tree, proj)


# ── the values the corpus declares, recorded as data ──────────

#: A corpus's benchmark script, in the shape the corpora actually ship: the
#: volatile-line list and the tolerance are module constants.
_CORPUS_SCRIPT = '''
import re

VOLATILE_PATTERNS = [
    re.compile(r"^\\s*DATE:\\s+\\d{1,2}/"),
    re.compile(r"^\\s*END TIME:"),
    re.compile(r"^\\d"),
]

RTOL = 0.02
ATOL = 0.25
'''


def _corpus_with_script(tmp: Path) -> Path:
    """A corpus that states its comparison rules AS DATA.

    It used to state them as a Python module the capture IMPORTED.  That put
    the corpus's rules in executable code the mechanism has to run, and it is
    why the three benchmark scripts could not be deleted: the rules lived
    only inside them.  The declaration is now read, for the reason the whole
    capture reads rather than imports — a file of values cannot execute.
    """
    proj = _one_per_dir_tree(tmp)
    (proj / 'benchmark_facts.json').write_text(json.dumps({
        'comparison': {
            'drop': [r'^\s*DATE:\s+\d{1,2}/', r'^\s*END TIME:', r'^\d'],
            'rtol': 0.02,
            'atol': 0.25,
        },
    }))
    return proj


def test_a_declaration_written_as_CODE_is_REFUSED(tmp_path):
    """A corpus's rules are values, and values are data.

    Measured: the rules lived only in the corpus's benchmark script, so the
    capture had to import it — which is what kept three per-corpus Python
    programs alive after the mechanism they drove was gone.  A declaration
    that would have to be executed is refused by name, so a corpus cannot
    silently reintroduce one.
    """
    tree, proj = _tree(tmp_path), _one_per_dir_tree(tmp_path)
    (proj / 'benchmark_facts.json').write_text(
        'VOLATILE_PATTERNS = [r"^DATE"]\n')

    with pytest.raises(ValueError, match='code'):
        capture_verification(_proto(), tree, tree, proj,
                             declaration=proj / 'benchmark_facts.json')


def test_the_volatile_lines_are_captured_from_the_corpus_not_invented(tmp_path):
    """Which lines are volatile is the CORPUS's declaration.

    It cannot be inferred from the outputs — a date differs from a date, and
    at the corpus's own tolerance a one-day difference is inside the numeric
    slack, so the comparison would call it a match.  It is read from the
    corpus's own benchmark script, which is where the corpus states it, and
    recorded as data so no run imports that script again.
    """
    tree, proj = _tree(tmp_path), _corpus_with_script(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj,
                             declaration=proj / 'benchmark_facts.json')

    lockstep = [a for a in v['assertions']
                if a['kind'] == 'text_lockstep'][0]
    assert lockstep['drop'] == [r'^\s*DATE:\s+\d{1,2}/', r'^\s*END TIME:',
                                r'^\d']


def test_the_tolerance_is_captured_from_the_corpus_too(tmp_path):
    """``rtol``/``atol`` are real measurements for the corpus that made them.

    Hardcoding them in the mechanism would move one corpus's numbers into the
    shared code and call that unification.
    """
    tree, proj = _tree(tmp_path), _corpus_with_script(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj,
                             declaration=proj / 'benchmark_facts.json')

    lockstep = [a for a in v['assertions']
                if a['kind'] == 'text_lockstep'][0]
    # NOT the mechanism's defaults (0.05/0.5): the fixture's values differ so
    # that a capture which ignored the corpus could not pass.
    assert lockstep['rtol'] == 0.02
    assert lockstep['atol'] == 0.25


def test_a_declaration_that_is_not_passed_is_not_searched_for(tmp_path):
    """The rules come from the caller, never from a guess.

    Searching would be how a capture reads ANOTHER corpus's rules: these
    scripts live with the tool rather than beside the corpus, so a search
    rooted at the project finds nothing and a search that widened until it
    found something would find a neighbour's.
    """
    tree, proj = _tree(tmp_path), _corpus_with_script(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    lockstep = [a for a in v['assertions']
                if a['kind'] == 'text_lockstep'][0]
    assert lockstep['drop'] == []


def test_a_corpus_with_no_script_declares_NO_volatile_lines(tmp_path):
    """An absent declaration is recorded as absent, not filled in.

    Inventing a pattern list would drop lines the corpus never said were
    volatile, which is a comparison that accepts a difference.
    """
    tree, proj = _tree(tmp_path), _one_per_dir_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    lockstep = [a for a in v['assertions']
                if a['kind'] == 'text_lockstep'][0]
    assert lockstep['drop'] == []


# ── restart chains: a catalogue passed in, never a corpus name in code ──

def test_no_catalogue_means_no_chains(tmp_path):
    """A corpus without one declares no chains — the honest answer.

    And the capture may not SEARCH for a catalogue: which corpus is being
    onboarded is the caller's fact, and a search would read another corpus's
    — the failure the declaration parameter already exists to prevent.
    """
    tree, proj = _tree(tmp_path), _one_per_dir_tree(tmp_path)

    v = capture_verification(_proto(), tree, tree, proj)

    assert v['chains'] == {}


def test_a_catalogue_is_read_and_needs_the_cases_to_be_there(tmp_path):
    """An entry naming a case the tree does not have is REFUSED.

    Dropping it silently is the failure mode: the chain would not be formed,
    and the case it belongs to is then judged on state that was never built —
    which is exactly how 19 of a corpus's 132 cases reported differences.
    """
    tree, proj = _tree(tmp_path), _one_per_dir_tree(tmp_path)
    facts = {'chains': {'carry': {'as': 'out'},
                        'order': {'A01-A': ['A01-B']}}}

    with pytest.raises(ValueError, match='does not have'):
        capture_verification(_proto(), tree, tree, proj, facts=facts)


def test_the_catalogue_names_which_artifact_is_handed_forward(tmp_path):
    """The carry rule travels with the table: order alone is half a chain."""
    tree, proj = _tree(tmp_path), _one_per_dir_tree(tmp_path)
    # The fixture's own cases: a catalogue about cases the tree does not have
    # is refused (pinned above), so this one names what is there.
    facts = {'chains': {'carry': {'as': 'sof1'},
                        'order': {'D02-01-1A': ['D01-00-0A']}}}

    v = capture_verification(_proto(), tree, tree, proj, facts=facts)

    assert v['chains']['carry'] == {'as': 'sof1'}
    assert v['chains']['order'] == {'D02-01-1A': ['D01-00-0A']}


def test_the_generic_module_names_no_corpus(tmp_path):
    """The catalogue's OWN data lives with the corpus, not in the extractor.

    Measured: the table was first written into this module, and every capture
    of every corpus then failed with "the restart catalogue names cases this
    corpus does not have" — one corpus's facts firing for all of them.
    """
    import ast
    from pathlib import Path as _P
    import alcorpora.onboard.extract as _extract
    # Located through the module, not by counting directory levels: this file
    # now lives beside the skill it tests, and a parents[N] anchor would have
    # to change with it.
    src = _P(_extract.__file__)
    tree = ast.parse(src.read_text())
    # A docstring explaining a past defect may name the corpus it was measured
    # on — that is documentation.  What must not exist is a corpus name in an
    # EXPRESSION, which is what a dispatch branch or a defaulted table is made
    # of, so docstrings are excluded exactly as the benchmark-contract guard
    # excludes them.
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, 'body', None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    bad = [(n.lineno, n.value[:60]) for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and id(n) not in docs
           and re.search(r'nastran|mystran|lapack', n.value, re.I)]
    assert not bad, f'the extractor names a corpus: {bad}'


# ── 3. a declared suite WIDTH must reach the plan ─────────────

def test_a_declared_suite_width_is_bound_into_the_plan(tmp_path):
    """The width a suite is measured at is a CORPUS declaration, and a width
    that never reaches the plan is not a width.

    The drivers are CPU-bound and contend, so a baseline recorded at one
    width and compared against a run at another is a different measurement —
    which is why the corpus pins it.  The plan must therefore both bind the
    value and supply it, and the two must agree.

    Measured defect: ``_verification_from_registry`` reads ``suite_jobs``
    from the rules, but ``_corpus_rules`` never emitted it, so a corpus that
    declared 8 had it read as 0 and the suite ran at whatever width it chose.
    The value was declared and nothing read it, which looks identical to not
    having declared it at all.
    """
    tree = _registered_tree(tmp_path)
    proto = _proto(tests=[{'name': 't', 'command': ['x']}])
    decl = tmp_path / 'benchmark_facts.json'
    decl.write_text(json.dumps({'suite_jobs': 8}))

    v = capture_verification(proto, tree, tree, None, declaration=decl)

    assert v['run']['jobs'] == 8
    assert v['run']['argv'][:3] == ['ctest', '-j', '{jobs}']


def test_a_corpus_that_declares_no_width_binds_NO_jobs(tmp_path):
    """A plan that bound ``{jobs}`` with no value would measure whatever the
    machine happens to have — ``problems()`` refuses exactly that, so the
    binding must be absent when the corpus declared no width."""
    tree = _registered_tree(tmp_path)
    proto = _proto(tests=[{'name': 't', 'command': ['x']}])

    v = capture_verification(proto, tree, tree, None)

    assert 'jobs' not in v['run']
    assert '{jobs}' not in v['run']['argv']
