import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { writePrivateJson, markAccountSession } from '../scripts/qingneng-account.mjs';

test('writePrivateJson 原子写入：内容回读、0o600、无 .auth-*.tmp 残留', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'qingneng-test-'));
  try {
    const target = path.join(dir, 'nested', 'auth.json');
    await writePrivateJson(target, { revision: 'abc', nested: { x: 1 } });
    assert.deepEqual(JSON.parse(await fs.readFile(target, 'utf8')), { revision: 'abc', nested: { x: 1 } });
    if (process.platform !== 'win32') {
      assert.equal((await fs.stat(target)).mode & 0o777, 0o600);
    }
    const entries = await fs.readdir(path.dirname(target));
    assert.ok(!entries.some((name) => name.startsWith('.auth-') && name.endsWith('.tmp')));
    // 覆盖写入同样是原子的。
    await writePrivateJson(target, { revision: 'second' });
    assert.deepEqual(JSON.parse(await fs.readFile(target, 'utf8')), { revision: 'second' });
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});

test('markAccountSession 通过 writePrivateJson 原子写入登录态标记', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'qingneng-session-'));
  try {
    const config = { authStatePath: path.join(dir, 'auth_state.json'), accountRevision: 'r1' };
    const context = { qingnengAccountChanged: true };
    await markAccountSession(context, config);
    const marker = path.join(dir, '.qingneng-session', 'config.json');
    assert.deepEqual(JSON.parse(await fs.readFile(marker, 'utf8')), { revision: 'r1' });
    assert.equal(context.qingnengAccountChanged, false);
    assert.ok(!(await fs.readdir(path.join(dir, '.qingneng-session'))).some((n) => n.endsWith('.tmp')));
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});
