import test from 'node:test';
import assert from 'node:assert/strict';
import { signUuid, signature, verifyToken } from '../src/tokens.js';

const SECRET = 'test-secret';
const UUID = '11111111-2222-3333-4444-555555555555';

test('sign then verify round-trips to the same uuid', () => {
  const token = signUuid(UUID, SECRET);
  assert.equal(verifyToken(token, SECRET), UUID);
});

test('token is <uuid>.<sig> shaped', () => {
  const token = signUuid(UUID, SECRET);
  assert.equal(token, `${UUID}.${signature(UUID, SECRET)}`);
  assert.match(token.split('.').pop(), /^[A-Za-z0-9_-]+$/); // base64url, no padding
});

test('tampered signature is rejected', () => {
  const token = signUuid(UUID, SECRET);
  const bad = token.slice(0, -1) + (token.endsWith('a') ? 'b' : 'a');
  assert.equal(verifyToken(bad, SECRET), null);
});

test('tampered uuid is rejected', () => {
  const token = signUuid(UUID, SECRET);
  const sig = token.split('.').pop();
  assert.equal(verifyToken(`99999999-2222-3333-4444-555555555555.${sig}`, SECRET), null);
});

test('wrong secret is rejected', () => {
  const token = signUuid(UUID, SECRET);
  assert.equal(verifyToken(token, 'other-secret'), null);
});

test('malformed tokens are rejected', () => {
  assert.equal(verifyToken('', SECRET), null);
  assert.equal(verifyToken('no-dot', SECRET), null);
  assert.equal(verifyToken('.sig', SECRET), null);
  assert.equal(verifyToken(null, SECRET), null);
  assert.equal(verifyToken(undefined, SECRET), null);
});

test('uuid containing dots verifies (lastIndexOf split)', () => {
  const weird = 'a.b.c';
  const token = signUuid(weird, SECRET);
  assert.equal(verifyToken(token, SECRET), weird);
});
