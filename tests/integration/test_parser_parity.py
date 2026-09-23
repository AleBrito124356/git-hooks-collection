"""PyYAML and the bundled mini-parser must read every config we ship identically.

The README promises that either parser handles .githooks.yaml. 0.1.0 broke that
promise on its own shipped file (`- wip:`); these tests keep it true.
"""

from __future__ import annotations

import itertools

import pytest
from _helpers import ROOT

from pre_commit_hooks import _core, _miniyaml, installer, registry

yaml = pytest.importorskip("yaml")


def both(text):
    return yaml.safe_load(text), _miniyaml.safe_load(text)


def test_shipped_config():
    pyyaml, mini = both((ROOT / ".githooks.yaml").read_text(encoding="utf-8"))
    assert pyyaml == mini


def test_template():
    pyyaml, mini = both(_core.TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert pyyaml == mini


SELECTIONS = [
    list(registry.NAMES),
    ["secrets"],
    ["secrets", "no-fixup"],
    ["conventional-commit", "issue-prefix"],
    [n for n in registry.NAMES if n != "tests"],
    *[list(pair) for pair in itertools.combinations(registry.NAMES, 2)][:12],
]


@pytest.mark.parametrize("selection", SELECTIONS, ids=lambda s: ",".join(s))
def test_installer_rendered_configs(selection):
    pyyaml, mini = both(installer.render_config(selection))
    assert pyyaml == mini
    assert sorted(n for names in mini["hooks"].values() for n in names) == sorted(selection)


EDGE_CASES = {
    "quoted strings keep specials": "a: \"x: y # not a comment\"\nb: 'single # quoted'\n",
    "flow lists": "a: [x, 'y z', 3, true]\nb: []\n",
    "scalars": "i: 42\nf: 3.5\nt: true\nn: null\ntilde: ~\ns: plain text\n",
    "regex in single quotes": "r: '^\\[[A-Z]+-\\d+\\]\\s*'\n",
    "nested mappings and lists": "a:\n  b:\n    - x\n    - y\n  c:\n    d: 1\n",
    "comments everywhere": "# top\na: 1  # trailing\n# middle\nb:\n  - x  # item\n",
    "empty string": "cmd: ''\nother: \"\"\n",
    "quoted list items": 'p:\n  - "fixup!"\n  - "wip:"\n  - \'WIP\'\n',
}


@pytest.mark.parametrize("text", EDGE_CASES.values(), ids=EDGE_CASES.keys())
def test_edge_cases(text):
    pyyaml, mini = both(text)
    assert pyyaml == mini


def test_unquoted_colon_item_is_the_known_divergence():
    # Why the template quotes "wip:": PyYAML reads it as a one-key mapping.
    pyyaml, mini = both("p:\n  - wip:\n")
    assert pyyaml == {"p": [{"wip": None}]}
    assert mini == {"p": ["wip:"]}
