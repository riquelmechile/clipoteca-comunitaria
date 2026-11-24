"""M34 strong-match and delivery contracts with no models or network."""

import pytest

from clipoteca.studio.selection import choose_result


def row(sim, score, *, strong=False):
    return {"status": "ok", "catalog_strong": strong, "clip": {"sim": sim, "score": score}}


@pytest.mark.parametrize(
    "catalog, imported, expected",
    [
        (row(0.27, 1, strong=True), row(0.9, 9), "catalog"),
        (row(0.2424, 2.44), row(0.4, 1), "imported"),
        (row(0.26, 3), row(0.25, 9), "catalog"),
        (row(0.25, 1), None, "catalog"),
        ({"status": "fail"}, row(0.3, 2), "imported"),
        (row(0.25, 1), {"status": "fail"}, "catalog"),
    ],
)
def test_selection_policy(catalog, imported, expected):
    assert choose_result(catalog, imported)[0] == expected
