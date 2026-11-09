import assert from 'node:assert/strict';
import test from 'node:test';
import { parseImportResponse, summarizeImport } from '../scripts/batch-plan.mjs';

test('known bare and enveloped responses keep per-file successes and failures', () => {
  const items = [{ date: '2026-11-09' }, { date: '2026-11-08', detail: '模板不匹配' }];
  for (const payload of [items, { retCode: 'T200', data: items }]) {
    const result = parseImportResponse(payload);
    assert.equal(result.succeeded.length, 1);
    assert.equal(result.failed[0].detail, '模板不匹配');
  }
});

test('documented empty results remain valid', () => {
  for (const payload of [[], { retCode: 'T200', data: [] }]) {
    assert.equal(parseImportResponse(payload).items.length, 0);
  }
});

for (const [index, payload] of [null, {}, true, 'ok', { data: [] },
  { retCode: 'T200' }, { retCode: 'T200', data: null },
  { retCode: 'T200', data: { unexpected: 'schema' } },
  { retCode: 'T400', data: [] }, [null], [true], [[]]].entries()) {
  test(`invalid response ${index} cannot be counted as success`, () => {
    assert.throws(() => parseImportResponse(payload), /结果格式异常.*可能已提交/);
  });
}

test('result summarization itself does not coerce unknown structures to an empty array', () => {
  assert.throws(() => summarizeImport({}), /结果格式异常/);
});
