"""Path-segment validation for caller-supplied Instagram identifiers."""

from __future__ import annotations

import pytest

from heliograph.instagram import endpoints as ep
from heliograph.instagram.service import InstagramService
from tests.instagram.fakes import FakeApi


@pytest.mark.parametrize("bad", ["../../accounts/logout", "1/../2", "%2e%2e", "a b", "", "1?x=1",
                                 "1#x", "１２３"])
@pytest.mark.parametrize("builder", [ep.user_info, ep.media_info, ep.media_comments,
                                     ep.collection_posts, ep.direct_thread, ep.like, ep.follow,
                                     ep.add_comment, ep.user_feed_by_pk])
def test_path_builders_reject_unsafe_segments(builder: object, bad: str) -> None:
    with pytest.raises(ValueError):
        builder(bad)  # type: ignore[operator]


def test_path_builders_accept_real_ids() -> None:
    assert ep.direct_thread("340282366841710300949128123456789") .endswith("789/")
    assert ep.media_info("3700000000000000001") == "/api/v1/media/3700000000000000001/info/"


async def test_thread_id_traversal_never_reaches_api() -> None:
    api = FakeApi()
    with pytest.raises(ValueError):
        await InstagramService(api).thread("../../web/likes/1/like")
    assert api.calls == []
