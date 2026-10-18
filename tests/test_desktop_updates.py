"""Disposable GitHub fixtures prove discovery/handoff without an installer or user profile."""

import copy

import httpx
import pytest

from sylvia_gm.interfaces import desktop_updates as updates

CURRENT = "0.3.0-alpha.1"


def release(version="0.3.0-alpha.2", *, prefix="v"):
    tag = prefix + version
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": "-" in version,
        "html_url": f"{updates.REPOSITORY_URL}/releases/tag/{tag}",
        "body": "A synthetic release.\n<script>doNotRun()</script>",
        "assets": [
            {
                "name": name,
                "state": "uploaded",
                "size": 123,
                "browser_download_url": f"{updates.REPOSITORY_URL}/releases/download/{tag}/{name}",
            }
            for name in (
                f"sylvia-gm-{version}-windows-x86_64-setup.exe",
                f"sylvia-gm-{version}-windows-x86_64-portable.zip",
                "SHA256SUMS.txt",
            )
        ],
    }


def check(releases, current=CURRENT):
    def respond(request):
        assert str(request.url) == updates.RELEASES_API
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, json=releases)

    return updates.check_for_update(current, transport=httpx.MockTransport(respond))


def test_semver_precedence_and_build_metadata():
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
        "1.0.1",
    ]
    versions = [updates.Version.parse(value) for value in ordered]
    shuffled = list(reversed(versions))
    assert sorted(shuffled) == versions
    assert updates.Version.parse("1.0.0+build.1") == updates.Version.parse("1.0.0+build.2")


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        "",
        "1.0",
        "v1.0.0",
        "01.0.0",
        "1.0.0-alpha.01",
        "1.0.0-",
        "1.0.0+",
        "1.0.0\n",
        "1.0.0/../bad",
        "1.0.0-α",
    ],
)
def test_invalid_versions_fail_clearly(value):
    with pytest.raises(updates.UpdateCheckError, match="version is invalid"):
        updates.Version.parse(value)


def test_discovery_selects_newest_in_same_named_channel_without_mutating_input():
    items = [
        release("0.3.0-alpha.2"),
        release("0.3.0-alpha.10"),
        release("0.2.9"),
        release("0.4.0-beta.1"),
        release("0.4.0-alpha.1"),
    ]
    items[-1]["draft"] = True
    before = copy.deepcopy(items)
    result = check(items)
    assert result["current_version"] == CURRENT
    assert result["channel"] == "alpha and stable"
    assert result["release"]["version"] == "0.3.0-alpha.10"
    assert items == before


@pytest.mark.parametrize(
    ("current", "available", "expected"),
    [
        (CURRENT, "0.3.0", "0.3.0"),
        ("0.3.0", "0.4.0-alpha.1", None),
        (CURRENT, CURRENT, None),
        (CURRENT, "0.2.0", None),
        (CURRENT, "0.3.0-beta.1", None),
        ("0.3.0-beta.1", "0.3.0-beta.2", "0.3.0-beta.2"),
    ],
)
def test_channel_and_no_update_behavior(current, available, expected):
    found = check([release(available)], current)["release"]
    assert (found["version"] if found else None) == expected


def test_release_without_v_prefix_keeps_exact_handoff_tag(monkeypatch):
    found = check([release(prefix="")])["release"]
    opened = []
    monkeypatch.setattr(updates.webbrowser, "open", lambda url, new: opened.append(url) or True)
    assert updates.open_update_release(CURRENT, found["tag"])["opened"]
    assert opened == [found["url"]]


def test_notes_remain_text_and_are_bounded():
    item = release()
    item["body"] = "<img src=x onerror=doNotRun()>" + "x" * updates.MAX_NOTES_CHARS
    result = check([item])["release"]
    assert result["notes"].startswith("<img")
    assert len(result["notes"]) == updates.MAX_NOTES_CHARS
    assert result["notes_truncated"] is True
    item["body"] = None
    assert check([item])["release"]["notes"] == ""


@pytest.mark.parametrize(
    "damage",
    [
        lambda r: r.update(html_url="https://evil.invalid/release"),
        lambda r: r.update(html_url=r["html_url"] + "?redirect=evil"),
        lambda r: r.update(html_url=r["html_url"].replace("github.com", "github.com.evil.invalid")),
        lambda r: r.update(body={"html": "invalid"}),
        lambda r: r.update(assets={}),
        lambda r: r.update(assets=[]),
        lambda r: r.update(prerelease=False),
        lambda r: r["assets"][0].update(browser_download_url="file:///tmp/untrusted.exe"),
        lambda r: r["assets"][0].update(browser_download_url="https://evil.invalid/setup.exe"),
        lambda r: r["assets"][0].update(size=True),
        lambda r: r["assets"][0].update(size=-1),
        lambda r: r["assets"][0].update(state="new"),
        lambda r: r["assets"].append(copy.deepcopy(r["assets"][0])),
        lambda r: r["assets"].pop(),
    ],
)
def test_invalid_distribution_metadata_cannot_offer_download(damage):
    item = release()
    damage(item)
    with pytest.raises(updates.UpdateCheckError):
        check([item])


def test_unknown_assets_and_non_version_tags_are_not_executable_candidates():
    item = release()
    item["assets"].extend([{"name": {}}, {"name": "unrelated.exe"}, None])
    assert check([{"draft": False, "prerelease": False, "tag_name": "notes"}, item])["release"][
        "version"
    ]


@pytest.mark.parametrize(
    "item", [{}, {"draft": "false"}, {"draft": False}, {"draft": False, "tag_name": "v1.0.0"}]
)
def test_incomplete_release_is_not_reported_as_up_to_date(item):
    with pytest.raises(updates.UpdateCheckError, match="incomplete release information"):
        check([item])


@pytest.mark.parametrize("payload", [{}, [None], ["bad"], 1])
def test_invalid_api_shape_is_failure_not_up_to_date(payload):
    with pytest.raises(updates.UpdateCheckError, match="invalid release information"):
        check(payload)


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500, 503])
def test_service_failure_is_retryable(status):
    def respond(request):
        return httpx.Response(status, headers={"location": "https://evil.invalid"})

    with pytest.raises(updates.UpdateCheckError, match="limiting|Could not reach"):
        updates.check_for_update(CURRENT, transport=httpx.MockTransport(respond))


def test_unreadable_or_oversized_response_is_rejected():
    for body in (b"not json", b"\xff", b" " * (updates.MAX_RESPONSE_BYTES + 1)):
        with pytest.raises(updates.UpdateCheckError, match="unreadable|too large"):
            updates.check_for_update(
                CURRENT,
                transport=httpx.MockTransport(
                    lambda request, content=body: httpx.Response(200, content=content)
                ),
            )


def test_interrupted_check_preserves_profile_and_can_retry(tmp_path, monkeypatch):
    profile = tmp_path / "disposable-profile"
    profile.mkdir()
    for name in ("campaign.sqlite3", "settings.yaml", "content-package.bin"):
        (profile / name).write_bytes(b"synthetic saved work")
    before = {p.name: p.read_bytes() for p in profile.iterdir()}
    opened = []
    monkeypatch.setattr(updates.webbrowser, "open", lambda *a, **k: opened.append(a))
    calls = []

    class InterruptedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"["
            raise httpx.ReadError("synthetic interrupted connection")

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=InterruptedBody())
        return httpx.Response(200, json=[release()])

    transport = httpx.MockTransport(respond)
    with pytest.raises(updates.UpdateCheckError, match="Could not reach"):
        updates.check_for_update(CURRENT, transport=transport)
    assert updates.check_for_update(CURRENT, transport=transport)["release"]["version"]
    assert len(calls) == 2
    assert opened == []
    assert {p.name: p.read_bytes() for p in profile.iterdir()} == before


def test_timeout_is_retryable():
    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with pytest.raises(updates.UpdateCheckError, match="Could not reach"):
        updates.check_for_update(CURRENT, transport=httpx.MockTransport(timeout))


@pytest.mark.parametrize(
    "tag",
    [
        "https://evil.invalid",
        "v0.2.0",
        "v0.3.0-beta.1",
        "v0.3.0-alpha.1",
        "v1.0.0/../../evil",
        "v1.0.0?redirect=evil",
    ],
)
def test_handoff_rejects_unsafe_or_ineligible_selection_without_opening_browser(tag, monkeypatch):
    monkeypatch.setattr(updates.webbrowser, "open", lambda *a, **k: pytest.fail("browser opened"))
    with pytest.raises(updates.UpdateCheckError):
        updates.open_update_release(CURRENT, tag)


def test_browser_failure_returns_fixed_official_page_for_manual_retry(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("synthetic unavailable browser")

    monkeypatch.setattr(updates.webbrowser, "open", unavailable)
    expected = f"{updates.REPOSITORY_URL}/releases/tag/v0.3.0-alpha.2"
    assert updates.open_update_release(CURRENT, "v0.3.0-alpha.2") == {
        "opened": False,
        "url": expected,
    }
    monkeypatch.setattr(updates.webbrowser, "open", lambda *a, **k: True)
    assert updates.open_update_release(CURRENT, "v0.3.0-alpha.2")["opened"] is True


def test_owner_routes_discovery_and_handoff_without_accessing_campaign_or_provider(monkeypatch):
    from sylvia_gm.interfaces.owner_process import _dispatch

    original = updates.check_for_update
    monkeypatch.setattr(
        updates,
        "check_for_update",
        lambda version: original(
            version,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[release()])),
        ),
    )
    opened = []
    monkeypatch.setattr(updates.webbrowser, "open", lambda url, new: opened.append(url) or True)
    dependencies = {
        "application_owner": None,
        "owner_id": "synthetic",
        "provider_resolver": None,
        "model_choices": None,
        "session_loop": None,
        "storage_root": None,
    }
    result = _dispatch(None, "check_desktop_update", {"current_version": CURRENT}, **dependencies)
    assert opened == []
    handoff = _dispatch(
        None,
        "open_desktop_update",
        {
            "current_version": CURRENT,
            "tag": result["release"]["tag"],
        },
        **dependencies,
    )
    assert handoff["opened"] is True
    assert opened == [result["release"]["url"]]
