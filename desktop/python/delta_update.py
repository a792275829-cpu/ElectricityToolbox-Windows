"""Shared digest, path validation and route selection for Windows deltas."""
from __future__ import annotations

import hashlib
import heapq
from pathlib import Path, PurePosixPath

from github_update import Cancelled, UpdateError, version_parts, trusted_url

FORMAT = 1


def cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled('已取消更新')


def digest(path, cancel=None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            cancelled(cancel)
            value.update(chunk)
    return value.hexdigest()


def safe_name(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\0' in name
            or name.startswith('/') or any(p in {'', '.', '..'} for p in name.split('/'))):
        raise UpdateError('补丁包含不安全的路径')
    return PurePosixPath(name)


def select_route(index, current, target, full_size, repository, platform='windows-x64'):
    """Smallest increasing-version route, always cheaper than the full installer."""
    if index.get('format') != FORMAT or index.get('version') != target:
        raise UpdateError('增量更新索引版本无效')
    edges = index.get('patches')
    if not isinstance(edges, list) or len(edges) > 2048:
        raise UpdateError('增量更新索引无效')
    graph = {}
    for edge in edges:
        try:
            base, end, size = edge['base'], edge['target'], edge['size']
            name = f'ElectricityToolbox-{base}-to-{end}-{platform}.patch.tar.xz'
            if (type(size) is not int or size <= 0 or edge['name'] != name
                    or not trusted_url(edge['browser_download_url'], repository)
                    or not edge['browser_download_url'].endswith('/' + name)
                    or not edge['digest'].startswith('sha256:')
                    or len(edge['digest']) != 71
                    or any(c not in '0123456789abcdef' for c in edge['digest'][7:])
                    or not version_parts(base) < version_parts(end) <= version_parts(target)):
                raise UpdateError('增量更新路径无效')
        except (KeyError, TypeError, AttributeError) as exc:
            raise UpdateError('增量更新路径无效') from exc
        graph.setdefault(base, []).append(edge)
    queue, costs, serial = [(0, 0, current, [])], {current: 0}, 0
    while queue:
        cost, _, node, route = heapq.heappop(queue)
        if cost != costs.get(node):
            continue
        if node == target:
            return route
        if len(route) >= 16:
            continue
        for edge in graph.get(node, []):
            new_cost, dest = cost + edge['size'], edge['target']
            if new_cost < min(full_size, costs.get(dest, full_size)):
                costs[dest] = new_cost
                serial += 1
                heapq.heappush(queue, (new_cost, serial, dest, route + [edge]))
    return []
