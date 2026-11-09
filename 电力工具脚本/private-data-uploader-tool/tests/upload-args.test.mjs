import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { parseFileArguments } from '../scripts/batch-plan.mjs';

const UPLOAD = new URL('../scripts/upload-private-data.mjs', import.meta.url);

test('--files a,b,c 逗号拆分', () => {
  assert.deepEqual(parseFileArguments(['a.xlsx,b.xlsx,c.xlsx']), ['a.xlsx', 'b.xlsx', 'c.xlsx']);
  assert.deepEqual(parseFileArguments([' a.xlsx , b.xlsx ,, c.xlsx ']), ['a.xlsx', 'b.xlsx', 'c.xlsx']);
});

test('重复 --files 传参方式保持兼容', () => {
  assert.deepEqual(parseFileArguments(['a.xlsx', 'b.xlsx', 'c.xlsx']), ['a.xlsx', 'b.xlsx', 'c.xlsx']);
});

test('Windows 含逗号的整体路径不被误拆', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'files-args-'));
  try {
    const weird = path.join(dir, '含,逗号,文件.xlsx');
    await fs.writeFile(weird, 'x');
    assert.deepEqual(parseFileArguments([weird]), [weird]);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});

test('upload-private-data.mjs 已接入独立锁模块、逗号拆分、登录分类与结构化错误', async () => {
  const source = await fs.readFile(UPLOAD, 'utf8');
  assert.match(source, /import \{ acquireAuthLock \} from '\.\/auth-lock\.mjs'/);
  assert.match(source, /import \{[^}]*writePrivateJson[^}]*\} from '\.\/qingneng-account\.mjs'/);
  // :693、:727 及另外两处的第 3 个 log 参数必须真正生效。
  const calls = source.match(/acquireAuthLock\(config[^)]*\)/g) ?? [];
  assert.equal(calls.length, 4, JSON.stringify(calls));
  assert.ok(calls.every((call) => call.includes('log')));
  // --files 分支按 Mac 口径做逗号拆分，同时保留 parseFileArguments 的含逗号路径保护。
  assert.match(source, /--files[\s\S]{0,240}parseFileArguments/);
  assert.match(source, /function loginFailure\(kind, message\)/);
  assert.match(source, /error\.login_kind = kind/);
  assert.match(source, /error\.login_kind && process\.env\.TOOLBOX_CLI_PROTOCOL/);
  // 登录态 JSON 改为原子写入。
  assert.match(source, /await writePrivateJson\(config\.authStatePath, await context\.storageState\(\)\)/);
  assert.doesNotMatch(source, /storageState\(\{ path: config\.authStatePath \}\)/);
  // 内联简化锁已删除。
  assert.doesNotMatch(source, /async function acquireAuthLock\(/);
  assert.doesNotMatch(source, /function isProcessAlive\(/);
});
