import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { acquireAuthLock } from '../scripts/auth-lock.mjs';

async function tempConfig() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'auth-lock-test-'));
  return { dir, config: { authStatePath: path.join(dir, 'auth_state.json') } };
}

test('acquireAuthLock 三参数：log 生效、写入持有者、inode 校验释放', async () => {
  const { dir, config } = await tempConfig();
  const logs = [];
  try {
    const release = await acquireAuthLock(config, 5000, (message) => logs.push(message));
    assert.ok(logs.some((m) => m.includes('已获得登录态锁')));
    const lockPath = `${config.authStatePath}.lock`;
    const owner = JSON.parse(await fs.readFile(lockPath, 'utf8'));
    assert.equal(owner.pid, process.pid);
    assert.equal(owner.script, 'private-data-uploader');
    if (process.platform !== 'win32') {
      assert.equal((await fs.stat(lockPath)).mode & 0o777, 0o600);
    }
    // 释放时按 inode/dev 校验：替换成别的锁文件后不会被误删。
    await release();
    await assert.rejects(fs.stat(lockPath));
    assert.deepEqual(await fs.readdir(dir), []);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});

test('stale lock（持有者进程已退出）由 .reap 串行清理后重新获得锁', async () => {
  const { dir, config } = await tempConfig();
  const lockPath = `${config.authStatePath}.lock`;
  try {
    await fs.writeFile(lockPath, JSON.stringify({ pid: 99999999, startedAt: new Date().toISOString() }), { mode: 0o600 });
    const logs = [];
    const release = await acquireAuthLock(config, 5000, (message) => logs.push(message));
    assert.ok(logs.some((m) => m.includes('已获得登录态锁')));
    assert.ok(!(await fs.readdir(dir)).some((name) => name.endsWith('.reap')));
    await release();
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});

test('锁被存活进程占用时按 timeout 抛错，且 log 提示等待', async () => {
  const { dir, config } = await tempConfig();
  const lockPath = `${config.authStatePath}.lock`;
  try {
    await fs.writeFile(lockPath, JSON.stringify({ pid: process.pid, startedAt: new Date().toISOString() }), { mode: 0o600 });
    const logs = [];
    await assert.rejects(
      acquireAuthLock(config, 300, (message) => logs.push(message)),
      /等待登录态锁超时/,
    );
    assert.ok(logs.some((m) => m.includes('检测到其他脚本正在使用同一登录态')));
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});
