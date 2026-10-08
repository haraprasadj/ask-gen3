"""Self-check for which repositories a build indexes.

    uv run python -m ingest.test_build

No network: plan() takes the GitHub listing as an argument. What is under test
is the licence rule, because index.db is published and served.
"""

from __future__ import annotations

from ingest import build

CFG = {"tier1": ["docs"], "tier2": ["fence"], "exclude": ["manifest"]}


def repo(spdx: str | None) -> dict:
    return {"license": {"spdx_id": spdx} if spdx else None}


LISTING = {
    "docs": repo("Apache-2.0"),
    "fence": repo("Apache-2.0"),
    "manifest": repo("Apache-2.0"),
    "tool": repo("MIT"),
    "unlicensed": repo(None),
    "unclear": repo("NOASSERTION"),
}


def test_only_openly_licensed_repos_are_indexed() -> None:
    got = build.plan(CFG, LISTING)
    assert got == {
        "docs": (1, "Apache-2.0"),
        "fence": (2, "Apache-2.0"),
        "tool": (3, "MIT"),
    }, got


def test_a_curated_repo_without_a_licence_is_skipped_too() -> None:
    listing = LISTING | {"fence": repo(None)}
    assert "fence" not in build.plan(CFG, listing, skip_tier3=True)


def test_only_and_skip_tier3_still_select() -> None:
    assert build.plan(CFG, LISTING, skip_tier3=True) == {
        "docs": (1, "Apache-2.0"),
        "fence": (2, "Apache-2.0"),
    }
    # --only takes curated tiers where known, tier 2 otherwise, and still
    # applies the licence rule; a name GitHub does not list has no licence.
    assert build.plan(CFG, LISTING, only={"docs", "tool", "unlicensed", "gone"}) == {
        "docs": (1, "Apache-2.0"),
        "tool": (2, "MIT"),
    }


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok — licence rule, tiers, --only, --skip-tier3")
