"""Shared GitHub release transport for the Windows updater."""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

REPOSITORY = "a792275829-cpu/ElectricityToolbox-Windows"
CHECK_TIMEOUT = 10
SHA256_DIGEST = re.compile(r"sha256:[a-fA-F0-9]{64}")


class UpdateError(ValueError):
    pass


class Cancelled(UpdateError):
    pass


def version_parts(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,3}", value):
        raise UpdateError("版本号无效")
    parts = tuple(map(int, value.removeprefix("v").split(".")))
    return parts + (0,) * (4 - len(parts))


def trusted_url(value: str, repository: str) -> bool:
    url = urlsplit(value)
    return (url.scheme == "https" and url.netloc == "github.com"
            and url.path.startswith(f"/{repository}/releases/"))


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    page: str
    asset: dict | None
    update_asset: dict | None = None


class ReleaseHTML(HTMLParser):
    """Read GitHub's public notes and per-asset SHA-256 without API quota."""
    def __init__(self):
        super().__init__()
        self.assets = []
        self.asset = None
        self.notes = []
        self.notes_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            self.asset = None
        if tag == "a" and "/releases/download/" in attrs.get("href", ""):
            self.asset = dict(browser_download_url=attrs["href"],
                              name=attrs["href"].rsplit("/", 1)[-1])
            self.assets.append(self.asset)
        if tag == "clipboard-copy" and self.asset is not None:
            # GitHub may render the asset digest as a copy button or as visible
            # text; keep whichever form the page exposes.
            value = attrs.get("value", "")
            if value:
                self.asset["digest"] = value
        if tag == "div":
            if self.notes_depth:
                self.notes_depth += 1
            elif "markdown-body" in attrs.get("class", "").split():
                self.notes_depth = 1
        if self.notes_depth and tag in {"p", "li", "br", "h1", "h2", "h3"}:
            self.notes.append("\n")

    def handle_endtag(self, tag):
        if tag == "li":
            self.asset = None
        if tag == "div" and self.notes_depth:
            self.notes_depth -= 1

    def handle_data(self, data):
        if self.notes_depth:
            self.notes.append(data)
        elif self.asset is not None and not self.asset.get("digest"):
            found = SHA256_DIGEST.search(data)
            if found:
                self.asset["digest"] = found.group(0)


class GitHubUpdater:
    def __init__(self, current: str, repository: str = REPOSITORY, opener=None):
        self.current = current
        self.repository = repository
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository):
            raise UpdateError("更新仓库配置无效")
        if opener is None:
            def opener(request, timeout):
                context = ssl.create_default_context()
                return urllib.request.urlopen(request, timeout=timeout, context=context)
        self.open = opener
        self.fallback_reason = None
        self.downloaded = None


    def remember_download(self, release, path):
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.downloaded = (release.version, path, checksum)
        return path

    def check(self) -> Release | None:
        raise UpdateError("请通过 Windows 更新器检查和安装更新。")


    def check_release(self):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"ElectricityToolbox/{self.current}",
                     "X-GitHub-Api-Version": "2022-11-28", "Cache-Control": "no-cache"},
        )
        try:
            with self.open(request, timeout=CHECK_TIMEOUT) as response:
                if response.status != 200:
                    raise UpdateError(f"更新服务返回错误（HTTP {response.status}）")
                data = json.loads(response.read(2 * 1024 * 1024))
            return self.parse_data(data)
        except (OSError, http.client.HTTPException) as exc:
            if isinstance(exc,urllib.error.HTTPError) and exc.code not in {403,429} and exc.code<500:
                message="更新仓库尚未发布正式版本" if exc.code==404 else f"更新服务返回错误（HTTP {exc.code}）"
                raise UpdateError(message) from exc
            detail=f"HTTP {exc.code}" if isinstance(exc,urllib.error.HTTPError) else str(exc)
            try:
                return self.check_public_release()
            except Exception as fallback:
                raise UpdateError(f"GitHub 更新接口不可用（{detail}）；备用检测失败：{fallback}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # A proxy or captive portal can answer 200 with non-JSON content.
            try:
                return self.check_public_release()
            except Exception as fallback:
                raise UpdateError(f"GitHub 版本信息无效；备用检测失败：{fallback}") from exc

    def check_public_release(self) -> Release | None:
        base = f"https://github.com/{self.repository}/releases"

        def request(url, method="GET", extra=None):
            headers = {"User-Agent": f"ElectricityToolbox/{self.current}", "Cache-Control": "no-cache"}
            headers.update(extra or {})
            return self.open(urllib.request.Request(url, method=method, headers=headers),
                             timeout=CHECK_TIMEOUT)

        def probe_size(url):
            try:
                with request(url, "HEAD") as response:
                    size = response.headers.get("Content-Length", "")
                    if response.status == 200 and size.isascii() and size.isdecimal() and int(size) > 0:
                        return int(size)
            except (OSError, http.client.HTTPException):
                pass
            # Some corporate proxies and CDN edges refuse HEAD; a single ranged
            # byte still reports the exact total, which the download must verify.
            with request(url, extra={"Range": "bytes=0-0"}) as response:
                if response.status not in {200, 206}:
                    raise UpdateError("GitHub 发布附件大小无效")
                size = response.headers.get("Content-Range", "").rpartition("/")[2] or \
                    response.headers.get("Content-Length", "")
                if not size.isascii() or not size.isdecimal() or int(size) <= 0:
                    raise UpdateError("GitHub 发布附件大小无效")
                return int(size)

        def read_html(response):
            raw = response.read(2 * 1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 2 * 1024 * 1024:
                raise UpdateError("GitHub 发布页面无效或过大")
            parser = ReleaseHTML()
            parser.feed(raw.decode("utf-8"))
            return parser

        with request(base + "/latest") as response:
            page = response.geturl()
            prefix = base + "/tag/"
            if not page.startswith(prefix):
                raise UpdateError("GitHub 最新版本跳转地址无效")
            tag = page[len(prefix):]
            if version_parts(tag) <= version_parts(self.current):
                return None
            notes = "".join(read_html(response).notes).strip()
        with request(base + "/expanded_assets/" + tag) as response:
            assets = read_html(response).assets
        names = self.release_asset_names(tag)
        primary = self.primary_asset_name(tag)
        selected = []
        for asset in assets:
            if asset["name"] not in names:
                continue
            # The delta index is optional. A page that cannot expose it must not
            # hide the verified full installer, matching the API channel.
            required = asset["name"] == primary
            url = asset["browser_download_url"]
            if url.startswith("/"):
                url = "https://github.com" + url
            if not url.startswith(base + "/download/" + tag + "/") or not SHA256_DIGEST.fullmatch(
                    asset.get("digest", "")):
                if required:
                    raise UpdateError("GitHub 发布附件链接或摘要无效")
                continue
            asset["browser_download_url"] = url
            try:
                asset["size"] = probe_size(url)
            except Exception:
                if required:
                    raise
                continue
            selected.append(asset)
        # A changed/empty HTML layout must not silently look like a successful check.
        if not any(a["name"] == primary for a in selected):
            raise UpdateError("GitHub 发布页面未找到可校验的安装包")
        return self.parse_data(dict(tag_name=tag, html_url=page, draft=False, prerelease=False,
                                   body=notes, assets=selected))
