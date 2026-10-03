"""The ``${TREE}`` placeholder and its expand/escape machinery.

Layer 1 (the transpiler).  A private protocol spells paths under ``${TREE}``,
but the *transpiler* only ever sees a compile database whose paths may carry
the same placeholder -- so the placeholder's definition belongs with the
transpiler, not with the protocol.  The protocol (layer 2) imports these;
``src.compile_commands`` (which the transpiler drives) imports them too, and
this module is what lets both do so without layer 1 reaching into layer 2.
"""

from __future__ import annotations

from pathlib import Path

#: The placeholder for "the tree this build was captured in".
#:
#: A real build records absolute paths, so a build replayed from a protocol
#: captured in one tree would generate sources into the OLD tree -- leaving
#: its own tree empty and then failing to compile a file that does exist,
#: just not where it is written down.
TREE = '${TREE}'


class ProtocolError(ValueError):
    """A protocol (or its ``${TREE}`` anchor) that cannot be read as one."""


def check_tree_invariant(text: str) -> str | None:
    """``None`` when *text* may hold placeholders, else why it may not.

    ``$`` is reserved, so a bare one is a malformed placeholder rather than
    a literal — it is indistinguishable from an escaping mistake and would
    expand into something nobody wrote.  A literal dollar is spelled ``$$``.

    Scanned character by character rather than with a pattern: a regex
    cannot skip an escape pair, so on ``a$$b`` it would examine the second
    ``$`` (which is followed by ``b``) and reject a perfectly legal spelling.
    """
    i = 0
    while i < len(text):
        if text[i] != '$':
            i += 1
            continue
        if text[i:i + 2] == '$$':
            i += 2
            continue
        if text[i:i + len(TREE)] == TREE:
            i += len(TREE)
            continue
        return (f'a bare "$" is reserved — write a literal dollar as "$$" '
                f'and the tree placeholder as {TREE!r}')
    return None


def expand_tree(text: str, tree: str | Path) -> str:
    """Replace :data:`TREE` with *tree*, in one left-to-right pass.

    Single-pass on purpose: a ``$`` introduced BY the tree must not be
    rescanned as another placeholder.

    A ``$`` that is neither the placeholder nor an escape is REFUSED rather
    than passed through.  Passing it through is what makes the failure
    silent: the text goes on to a build system or a shell, which spells its
    own variables the same way, reads it as one of theirs, and expands it to
    nothing — so a path loses its root and the build proceeds as if that
    were the answer.  See :func:`check_tree_invariant`, which is the same
    test this applies.
    """
    why = check_tree_invariant(text)
    if why:
        raise ProtocolError(f'cannot expand {text!r}: {why}')
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i:i + 2] == '$$':
            out.append('$')
            i += 2
        elif text[i:i + len(TREE)] == TREE:
            out.append(str(tree))
            i += len(TREE)
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def escape_tree(text: str, tree: str | Path | None = None) -> str:
    """The inverse of :func:`expand_tree`.

    A command recorded verbatim keeps the absolute paths of the tree it was
    captured FROM, so a build generated in a new tree would write its
    generated sources back into the old one.  Capturing re-roots them.

    The match is a whole path component: ``/treehouse`` and ``/tree/xyz``
    are different paths from ``/tree``, and replacing inside them would
    corrupt an unrelated absolute path.
    """
    if tree is None:
        return text.replace('$', '$$')
    prefix = str(tree).rstrip('/')
    if not prefix:
        return text.replace('$', '$$')
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i:i + 2] == '$$':
            out.append('$$')
            i += 2
            continue
        if text.startswith(prefix, i):
            end = i + len(prefix)
            if end == len(text) or text[end] == '/':
                out.append(TREE)
                i = end
                continue
        if text[i] == '$':
            out.append('$$')
            i += 1
            continue
        out.append(text[i])
        i += 1
    return ''.join(out)


def capture_tree(text: str, tree: str | Path) -> str:
    """Alias for :func:`escape_tree` with a tree, named for its use."""
    return escape_tree(text, tree)
