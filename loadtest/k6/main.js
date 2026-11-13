// The load-test mix (#281, docs/loadtest.md). Run by loadtest/run.py in the lt-k6 container:
//   k6 run -e SCENARIO=<smoke|steady|stress|soak|billing-night|prod-shaped> -e RUN=<dir> main.js
// Every request carries the tag `grp` (read, list, write, auth, ui, mcp, usage-post); the
// thresholds are per group: p95 < 1 s, p99 < 1.5 s, and < 0.1 % failed requests overall.
// Rates are operations per second at 1x, scaled by MULT; open model (arrival rate).
import http from 'k6/http';
import exec from 'k6/execution';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';

const BASE = __ENV.BASE || 'http://lt-app:8080';
const MCP = __ENV.MCP || 'http://lt-mcp:8080/mcp';
const RUN = __ENV.RUN || 'adhoc';
const SCENARIO = __ENV.SCENARIO || 'smoke';
const MULT = Number(__ENV.MULT || 1);
const DURATION = __ENV.DURATION || '30m';
const STEP = __ENV.STEP || '10m'; // stress: minutes per load step
const STEPS = (__ENV.STEPS || '1,2,3,5').split(',').map(Number);
const PASSWORD = __ENV.LOADTEST_OPERATOR_PASSWORD;
// several service tokens: bss-mcp limits each principal to 60 calls a minute
const MCP_TOKENS = (__ENV.LOADTEST_MCP_TOKENS || '').split(',').filter(Boolean);
const RUN_TAG = __ENV.RUN_TAG || '0'; // names of the run's new subscribers

export const GROUPS = ['read', 'list', 'write', 'auth', 'ui', 'mcp', 'usage-post'];
// rates per second at 1x (the human's peak, 2026-11-09); list is inside the read budget
const RATES = { read: 25, list: 5, write: 5, mcp: 2, 'usage-post': 5 };
const UI_VUS = 20;

// Share individual ids/rows. A single object containing every pool would be copied in
// full on every DATA[0] access, exhausting the generator's 2 GiB budget at the 1x mix.
const DATA = {};
for (const key of ['customers', 'accounts', 'products', 'bills', 'buckets', 'postpaid', 'changes', 'terminations']) {
  DATA[key] = new SharedArray(key, () => JSON.parse(open(`/out/${RUN}/data.json`))[key] || []);
}
const d = () => DATA;

const P = {
  customer: '/tmf-api/customer/v5/customer',
  individual: '/tmf-api/partyManagement/v5/individual',
  account: '/tmf-api/accountManagement/v5/billingAccount',
  product: '/tmf-api/productInventory/v5/product',
  bucket: '/tmf-api/prepayBalanceManagement/v4/bucket',
  topup: '/tmf-api/prepayBalanceManagement/v4/topupBalance',
  bill: '/tmf-api/customerBillManagement/v5/customerBill',
  order: '/tmf-api/productOrderingManagement/v5/productOrder',
  offering: '/tmf-api/productCatalogManagement/v5/productOffering',
  payment: '/tmf-api/payment/v4/payment',
  resource: '/tmf-api/resourceInventory/v5/resource',
  usage: '/tmf-api/productUsageManagement/v5/productUsage',
  internal: '/internal/v1/billingAccount',
  session: '/internal/v1/session',
};

// ---------- options ----------

function thresholds(steps) {
  const t = {
    http_req_failed: [{ threshold: 'rate<0.001', abortOnFail: false }],
    checks: ['rate>0.999'],
    dropped_iterations: ['count==0'],
  };
  for (const g of GROUPS) {
    t[`http_req_duration{grp:${g}}`] = ['p(95)<1000', 'p(99)<1500'];
    t[`http_req_failed{grp:${g}}`] = ['rate<0.001'];
    for (const s of steps) {
      // per load step: the report names the first step and group that break a target
      t[`http_req_duration{grp:${g},step:${s}}`] = ['p(95)<1000', 'p(99)<1500'];
      t[`http_req_failed{grp:${g},step:${s}}`] = ['rate<0.001'];
    }
  }
  return t;
}

function rate(name, mult, duration) {
  const r = RATES[name] * mult;
  return {
    executor: 'constant-arrival-rate',
    exec: name.replace('-', '_'),
    rate: Math.max(1, Math.round(r * 100)),
    timeUnit: '100s', // keeps fractional rates (0.2x) exact
    duration,
    preAllocatedVUs: Math.ceil(r * 2) + 2,
    maxVUs: Math.ceil(r * 20) + 10,
    tags: { grp: name },
  };
}

function mix(mult, duration) {
  const s = {};
  for (const name of Object.keys(RATES)) s[name] = rate(name, mult, duration);
  s.ui = {
    executor: 'constant-vus',
    exec: 'ui',
    vus: Math.max(1, Math.round(UI_VUS * mult)),
    duration,
    tags: { grp: 'ui' },
  };
  return s;
}

function stressMix() {
  // each step: every rate-based group ramps to RATES x m for STEP, ui VUs likewise
  const s = {};
  const stages = (base) => STEPS.flatMap((m) => [
    { target: Math.max(1, Math.round(base * m * 100)), duration: '30s' },
    { target: Math.max(1, Math.round(base * m * 100)), duration: STEP },
  ]);
  for (const name of Object.keys(RATES)) {
    const top = RATES[name] * STEPS[STEPS.length - 1];
    s[name] = {
      executor: 'ramping-arrival-rate',
      exec: name.replace('-', '_'),
      startRate: Math.round(RATES[name] * STEPS[0] * 100),
      timeUnit: '100s',
      stages: stages(RATES[name]),
      preAllocatedVUs: Math.ceil(top * 2) + 2,
      maxVUs: Math.ceil(top * 20) + 10,
      tags: { grp: name },
    };
  }
  s.ui = {
    executor: 'ramping-vus',
    exec: 'ui',
    startVUs: UI_VUS * STEPS[0],
    stages: STEPS.flatMap((m) => [
      { target: UI_VUS * m, duration: '30s' },
      { target: UI_VUS * m, duration: STEP },
    ]),
    gracefulRampDown: '10s',
    tags: { grp: 'ui' },
  };
  return s;
}

function scenarios() {
  switch (SCENARIO) {
    case 'smoke':
      return { smoke: { executor: 'per-vu-iterations', exec: 'smoke', vus: 1, iterations: 1 } };
    case 'stress':
      return stressMix();
    case 'soak':
      return mix(MULT, __ENV.DURATION || '3h');
    case 'billing-night':
      // 20 % of the mix, and one catch-up of the next run date through the internal API
      return {
        ...mix(MULT === 1 ? 0.2 : MULT, __ENV.DURATION || '120m'),
        catchup: { executor: 'per-vu-iterations', exec: 'catchup', vus: 1, iterations: 1, startTime: '30s' },
      };
    default: // steady, prod-shaped
      return mix(MULT, DURATION);
  }
}

const STEP_MINUTES = parseInt(STEP, 10);
export const options = {
  scenarios: scenarios(),
  thresholds: thresholds(SCENARIO === 'stress' ? STEPS : []),
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max', 'count'],
  discardResponseBodies: false,
  setupTimeout: '120s',
  noConnectionReuse: false,
  noCookiesReset: true,
  userAgent: 'bss-loadtest-k6',
};

// ---------- helpers ----------

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const uuid = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
  const r = (Math.random() * 16) | 0;
  return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
});
const started = Date.now();

function step() {
  if (SCENARIO !== 'stress') return '0';
  const minutes = (Date.now() - started) / 60000;
  const per = STEP_MINUTES + 0.5;
  return String(STEPS[Math.min(STEPS.length - 1, Math.floor(minutes / per))]);
}

function tags(grp, name) {
  return { grp, name, step: step() };
}

function forwarded() {
  // a distinct client address per VU and scenario, as Caddy reports operators' addresses
  const id = exec.vu.idInTest;
  return `10.${(id >> 16) & 255}.${(id >> 8) & 255}.${id & 255}`;
}

function headers(write) {
  const h = { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Forwarded-For': forwarded() };
  if (write) h['X-Bss-Request'] = '1';
  return h;
}

function ok(res, what, expect) {
  const good = check(res, { [what]: (r) => (expect ? expect.includes(r.status) : r.status >= 200 && r.status < 300) });
  if (!good && __ENV.VERBOSE) console.warn(`${what}: ${res.status} ${String(res.body).slice(0, 300)}`);
  return good;
}

function get(grp, name, url) {
  const res = http.get(BASE + url, { headers: headers(false), tags: tags(grp, name) });
  ok(res, `${grp} ${name}`);
  return res;
}

function post(grp, name, url, body, key) {
  const h = headers(true);
  if (key) h['Idempotency-Key'] = key;
  const res = http.post(BASE + url, JSON.stringify(body), { headers: h, tags: tags(grp, name) });
  ok(res, `${grp} ${name}`);
  return res;
}

const OPERATORS = 20;
const operator = (i) => `lt-op-${String((i % OPERATORS) + 1).padStart(2, '0')}`;

function signInRequest(user, extra) {
  return http.post(BASE + P.session, JSON.stringify({ username: user, password: PASSWORD }), {
    headers: { ...headers(true), ...(extra || {}) },
    tags: tags('auth', 'sign-in'),
  });
}

// The API groups share the 20 operators' sessions that setup() signs in one at a time. A
// sign-in costs 16 MiB of scrypt: a burst of one per VU at the start (50+ at once)
// OOM-killed lt-app's processes at the load-test budget (docs/loadtest-results.md), and
// operators sign in once a shift, not once per request stream.
let shared = null;
function useSession(data) {
  if (shared === null) shared = data.sessions[exec.vu.idInTest % data.sessions.length];
  http.cookieJar().set(BASE, 'bss_session', shared);
}

// ui VUs sign in themselves, staggered half a second apart, then keep their session
let uiSignedIn = false;
function uiSignIn() {
  if (uiSignedIn) return;
  sleep((exec.vu.idInTest % 200) * 0.5);
  uiSignedIn = ok(signInRequest(operator(exec.vu.idInTest)), 'auth sign-in');
}

// ---------- setup: the catalog ids ----------

export function setup() {
  const jar = http.cookieJar();
  const res = http.post(BASE + P.session, JSON.stringify({ username: 'lt-admin', password: PASSWORD }), {
    headers: headers(true),
    tags: { grp: 'setup' },
  });
  if (res.status !== 200 && res.status !== 201) throw new Error(`setup sign-in: ${res.status} ${res.body}`);
  const offerings = {};
  for (const code of ['MOB-S', 'MOB-L']) {
    const r = http.get(`${BASE}${P.offering}?externalIdentifier.id=${code}`, { headers: headers(false), tags: { grp: 'setup' } });
    offerings[code] = r.json()[0].id;
  }
  jar.clear(BASE);
  const sessions = [];
  for (let i = 0; i < OPERATORS; i += 1) {
    const r = signInRequest(operator(i), { 'X-Forwarded-For': `10.255.0.${i + 1}` });
    if (!ok(r, 'auth sign-in')) throw new Error(`setup sign-in ${operator(i)}: ${r.status}`);
    sessions.push(r.cookies.bss_session[0].value);
    jar.clear(BASE);
  }
  return { offerings, sessions };
}

// ---------- the groups ----------

export function read(data) {
  useSession(data);
  const kind = Math.floor(Math.random() * 5);
  if (kind === 0) get('read', 'customer', `${P.customer}/${pick(d().customers)}`);
  else if (kind === 1) get('read', 'billingAccount', `${P.account}/${pick(d().accounts)}`);
  else if (kind === 2) get('read', 'product', `${P.product}/${pick(d().products)}`);
  else if (kind === 3) get('read', 'bucket', `${P.bucket}/${pick(d().buckets)[0]}`);
  else get('read', 'bill', `${P.bill}/${pick(d().bills)}`);
}

export function list(data) {
  useSession(data);
  const offset = Math.random() < 0.5 ? 0 : Math.floor(Math.random() * 250) * 20; // <= 5000
  const kind = Math.floor(Math.random() * 5);
  if (kind === 0) get('list', 'customers', `${P.customer}?offset=${offset}&limit=20`);
  else if (kind === 1) get('list', 'billingAccounts', `${P.account}?offset=${offset}&limit=20`);
  else if (kind === 2) get('list', 'products', `${P.product}?offset=${offset}&limit=20`);
  else if (kind === 3) get('list', 'bills', `${P.bill}?offset=${offset}&limit=20`);
  else get('list', 'productUsage', `${P.usage}?product.id=${pick(d().products)}&limit=20`);
}

function tomorrowNoon() {
  const t = new Date(Date.now() + 86400000);
  return `${t.toISOString().slice(0, 10)}T12:00:00Z`;
}

function topup() {
  const [bucket, account] = pick(d().buckets);
  post('write', 'topup', P.topup, {
    '@type': 'TopupBalance',
    amount: { amount: 10, units: 'USD' },
    usageType: 'monetary',
    bucket: { id: bucket },
    partyAccount: { id: account, '@referredType': 'BillingAccount' },
  }, uuid());
}

function payment() {
  const account = pick(d().postpaid);
  post('write', 'payment', P.payment, {
    '@type': 'Payment',
    account: { id: account, href: `${P.account}/${account}`, '@referredType': 'BillingAccount' },
    totalAmount: { unit: 'USD', value: 10.0 },
    paymentMethod: { name: 'bankTransfer', '@type': 'BankAccountTransfer' },
  }, uuid());
}

const ADDRESS = { street1: '1 Market St', city: 'San Francisco', stateOrProvince: 'CA', postCode: '94105' };
let made = 0;
function newSubscriber(data) {
  // party, customer, postpaid account, number, order: a new subscriber with one line
  const n = `k6-${RUN_TAG}-${exec.vu.idInTest}-${made}`;
  // a random number in area codes 400-999 (clear of the dataset's 1302...), never ending in
  // the stub network's test suffixes 0000-0002; a clash with an earlier run is ~1e-6
  const area = 400 + Math.floor(Math.random() * 600);
  const rest = Math.floor(Math.random() * 1000) * 10000 + 3 + Math.floor(Math.random() * 9997);
  const msisdn = `1${area}${String(rest).padStart(7, '0')}`;
  made += 1;
  const party = post('write', 'individual', P.individual, { '@type': 'Individual', givenName: 'Load', familyName: n });
  if (party.status !== 201) return;
  const customer = post('write', 'customer', P.customer, {
    '@type': 'Customer', name: n, status: 'Active',
    engagedParty: { id: party.json().id, '@type': 'PartyRef', '@referredType': 'Individual' },
  });
  if (customer.status !== 201) return;
  const account = post('write', 'billingAccount', P.account, {
    '@type': 'BssBillingAccount', '@baseType': 'BillingAccount', name: n, currency: 'USD',
    contact: [{ '@type': 'Contact', contactType: 'serviceAddress',
      contactMedium: [{ '@type': 'GeographicAddressContactMedium', country: 'US', ...ADDRESS }] }],
    relatedParty: [{ '@type': 'RelatedPartyRefOrPartyRoleRef', role: 'customer',
      partyOrPartyRole: { '@type': 'PartyRoleRef', id: customer.json().id, name: n, '@referredType': 'Customer' } }],
  });
  if (account.status !== 201) return;
  const number = post('write', 'resource', P.resource, {
    '@type': 'LogicalResource', name: `MSISDN ${msisdn}`, value: msisdn, administrativeState: 'unlocked',
    resourceSpecification: { id: 'msisdn', '@type': 'ResourceSpecificationRef' },
  });
  if (number.status !== 201) return;
  post('write', 'order.add', P.order, {
    '@type': 'ProductOrder',
    billingAccount: { id: account.json().id, '@type': 'BillingAccountRef' },
    requestedStartDate: tomorrowNoon(),
    productOrderItem: [{
      '@type': 'ProductOrderItem', id: '1', action: 'add',
      productOffering: { id: data.offerings['MOB-S'], '@type': 'ProductOfferingRef' },
      product: { '@type': 'ProductRefOrValue', realizingResource: [{ id: number.json().id, '@type': 'ResourceRef', '@referredType': 'LogicalResource' }] },
    }],
  }, uuid());
}

function lineOrder(name, account, item) {
  post('write', name, P.order, {
    '@type': 'ProductOrder',
    billingAccount: { id: account, '@type': 'BillingAccountRef' },
    requestedStartDate: tomorrowNoon(),
    productOrderItem: [{ '@type': 'ProductOrderItem', id: '1', ...item }],
  }, uuid());
}

function planChange(data) {
  const pool = d().changes;
  const [product, account, offering] = pool[exec.scenario.iterationInTest % pool.length];
  const other = offering === data.offerings['MOB-S'] ? data.offerings['MOB-L'] : data.offerings['MOB-S'];
  lineOrder('order.modify', account, {
    action: 'modify',
    product: { id: product, '@type': 'ProductRef' },
    productOffering: { id: other, '@type': 'ProductOfferingRef' },
  });
}

function termination() {
  const pool = d().terminations;
  const [product, account] = pool[exec.scenario.iterationInTest % pool.length];
  lineOrder('order.delete', account, { action: 'delete', product: { id: product, '@type': 'ProductRef' } });
}

export function write(data) {
  useSession(data);
  const r = Math.random() * 100;
  if (r < 40) topup();
  else if (r < 65) payment();
  else if (r < 85) newSubscriber(data);
  else if (r < 95) planChange(data);
  else termination();
}

export function usage_post(data) {
  useSession(data);
  post('usage-post', 'productUsage', P.usage, {
    '@type': 'ProductUsage',
    usageSpecification: { id: 'data', '@type': 'ProductUsageSpecificationRef' },
    product: { id: pick(d().products), '@type': 'ProductRef', '@referredType': 'Product' },
    usageCharacteristic: [
      { '@type': 'IntegerCharacteristic', name: 'quantity', valueType: 'integer', value: 1048576 },
    ],
    externalIdentifier: [{ '@type': 'ExternalIdentifier', owner: 'mediation', id: `k6-${RUN_TAG}-${uuid()}` }],
  });
}

const MCP_TOOLS = [
  ['get_customer', () => ({ customer_id: pick(d().customers) })],
  ['get_account', () => ({ account_id: pick(d().accounts) })],
  ['get_product', () => ({ product_id: pick(d().products) })],
  ['get_bucket', () => ({ bucket_id: pick(d().buckets)[0] })],
  ['get_bill', () => ({ bill_id: pick(d().bills) })],
];

let mcpId = 0;
export function mcp() {
  const [tool, args] = pick(MCP_TOOLS);
  mcpId += 1;
  const res = http.post(MCP, JSON.stringify({
    jsonrpc: '2.0', id: mcpId, method: 'tools/call', params: { name: tool, arguments: args() },
  }), {
    headers: { Authorization: `Bearer ${MCP_TOKENS[exec.scenario.iterationInTest % MCP_TOKENS.length]}`, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    tags: tags('mcp', tool),
  });
  check(res, {
    [`mcp ${tool}`]: (r) => {
      if (r.status !== 200) return false;
      try {
        const result = r.json().result;
        const inner = JSON.parse(result.content[0].text);
        return !result.isError && inner.status >= 200 && inner.status < 300;
      } catch (e) {
        return false;
      }
    },
  });
}

// the account card: the account first, then its panels in parallel, as the UI does
function accountCard(account) {
  const h = { headers: headers(false), tags: tags('ui', 'account') };
  const res = http.get(`${BASE}${P.account}/${account}`, h);
  if (!ok(res, 'ui account')) return;
  const body = res.json();
  const customer = ((body.relatedParty || []).find((p) => p.role === 'customer') || {}).partyOrPartyRole;
  const reqs = [
    ['products', `${P.internal}/${account}/products`],
    ['settlement', `${P.internal}/${account}/settlement`],
    ['charges', `${P.internal}/${account}/charges?limit=20`],
    ['transactions', `${P.internal}/${account}/transactions?limit=20`],
  ];
  if (customer) reqs.push(['customer', `${P.customer}/${customer.id}`]);
  if (body.ratingType === 'prepaid') reqs.push(['buckets', `${P.bucket}?partyAccount.id=${account}`]);
  const out = http.batch(reqs.map(([name, url]) => ['GET', BASE + url, null, { headers: headers(false), tags: tags('ui', name) }]));
  out.forEach((r, i) => ok(r, `ui ${reqs[i][0]}`));
}

export function ui() {
  uiSignIn();
  const kind = Math.random();
  if (kind < 0.7) accountCard(pick(d().accounts));
  else get('ui', 'billingAccounts', `${P.account}?offset=0&limit=20`);
  sleep(5 + Math.random() * 10);
}

// billing-night: the operator's catch-up of CATCHUP_DATE (the worker runs it)
export function catchup() {
  const res = http.post(BASE + P.session, JSON.stringify({ username: 'lt-admin', password: PASSWORD }), {
    headers: headers(true), tags: { grp: 'setup' },
  });
  if (res.status >= 300) throw new Error(`catch-up sign-in: ${res.status}`);
  const run = http.post(`${BASE}/internal/v1/billingRun/catchUp`, JSON.stringify({ upTo: __ENV.CATCHUP_DATE }), {
    headers: headers(true), tags: { grp: 'setup' },
  });
  check(run, { 'catch-up queued': (r) => r.status === 202 });
  if (run.status !== 202) console.error(`catch-up: ${run.status} ${run.body}`);
}

// smoke: every request type once, all must pass
export function smoke(data) {
  useSession(data);
  uiSignIn();
  get('read', 'customer', `${P.customer}/${d().customers[0]}`);
  get('read', 'billingAccount', `${P.account}/${d().accounts[0]}`);
  get('read', 'product', `${P.product}/${d().products[0]}`);
  get('read', 'bucket', `${P.bucket}/${d().buckets[0][0]}`);
  get('read', 'bill', `${P.bill}/${d().bills[0]}`);
  get('list', 'customers', `${P.customer}?offset=4980&limit=20`);
  get('list', 'billingAccounts', `${P.account}?offset=0&limit=20`);
  get('list', 'products', `${P.product}?offset=100&limit=20`);
  get('list', 'bills', `${P.bill}?offset=0&limit=20`);
  get('list', 'productUsage', `${P.usage}?product.id=${d().products[0]}&limit=20`);
  topup();
  payment();
  newSubscriber(data);
  planChange(data);
  termination();
  usage_post(data);
  for (const [tool, args] of MCP_TOOLS) {
    const res = http.post(MCP, JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args() } }), {
      headers: { Authorization: `Bearer ${MCP_TOKENS[0]}`, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
      tags: tags('mcp', tool),
    });
    check(res, { [`mcp ${tool}`]: (r) => r.status === 200 && !r.json().result.isError && JSON.parse(r.json().result.content[0].text).status < 300 });
  }
  accountCard(d().accounts[0]);
  accountCard(d().buckets[0][1]);
}

export function handleSummary(data) {
  return {
    [`/out/${RUN}/k6-summary.json`]: JSON.stringify(data, null, 1),
    stdout: `k6 ${SCENARIO}: ${data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0} requests\n`,
  };
}
