import { describe, expect, it } from 'vitest';
import { billingStatusValue } from '../format/labels';
import { json, stubFetch, tmfError } from '../test/fetch';
import { ApiError } from './client';
import { loadBalanceActions } from './history';
import { probe } from './hubs';
import { toMessage } from './messages';
import { loadRealizingNumbers, requiresNumber } from './numbers';
import { toParty } from './parties';
import { contactFields } from './partyDocuments';
import { addressErrors } from './serviceAddress';
import { findReceiver } from './transfer';
import { toRecord } from './usage';

// What the API layer makes of answers the screens' own tests do not send: missing fields
// refused with the error the screen shows, the fallbacks of older bodies, and lookups
// that find nothing.

const signal = () => new AbortController().signal;

describe('toParty', () => {
  it('names an individual by the parts it has, an organization by its name', () => {
    expect(
      toParty('Individual', { id: 'i1', givenName: 'Olga', familyName: 'Kuznetsova' }),
    ).toEqual({
      id: 'i1',
      kind: 'Individual',
      name: 'Olga Kuznetsova',
      givenName: 'Olga',
      familyName: 'Kuznetsova',
      status: '',
    });
    expect(
      toParty('Individual', { id: 'i2', familyName: 'Ivanov', status: 'validated' }),
    ).toMatchObject({
      name: 'Ivanov',
      givenName: '',
      status: 'validated',
    });
    expect(toParty('Organization', { id: 'o1', name: 'Romashka LLC' })).toMatchObject({
      name: 'Romashka LLC',
      givenName: '',
      familyName: '',
    });
  });

  it('refuses a party without an id, or an organization without a name', () => {
    expect(() => toParty('Individual', { givenName: 'Olga' })).toThrow(
      'The server sent no Individual id',
    );
    expect(() => toParty('Organization', { id: 'o1' })).toThrow(
      'The server sent no organization name',
    );
  });
});

describe('contactFields', () => {
  it('reads media typed by @type alone, as older parties carry them', () => {
    expect(
      contactFields([
        { '@type': 'EmailContactMedium', emailAddress: 'a@example.test' },
        { '@type': 'PhoneContactMedium', phoneNumber: '+15125550100' },
        { '@type': 'GeographicAddressContactMedium', city: 'Austin', country: 'US' },
      ]),
    ).toEqual({
      email: 'a@example.test',
      phone: '+15125550100',
      street1: '',
      city: 'Austin',
      postCode: '',
      country: 'US',
    });
  });

  it('is empty without media', () => {
    expect(contactFields(undefined)).toEqual({
      email: '',
      phone: '',
      street1: '',
      city: '',
      postCode: '',
      country: '',
    });
  });
});

describe('toRecord', () => {
  const record = {
    id: 'u1',
    usageType: 'data',
    usageCharacteristic: [
      { name: 'quantity', value: 1024 },
      { name: 'eventTime', value: '2026-11-26T10:00:00Z' },
    ],
  };

  it.each([
    [{ ...record, id: undefined }, 'the API sent a usage without an id or a type'],
    [{ ...record, usageType: undefined }, 'the API sent a usage without an id or a type'],
    [{ ...record, usageCharacteristic: [] }, 'the API sent a usage without a quantity or a time'],
    [
      { ...record, usageCharacteristic: [{ name: 'quantity', value: 1 }] },
      'the API sent a usage without a quantity or a time',
    ],
    [
      { ...record, usageProductPrice: [{ name: 'Data', price: { dutyFreeAmount: { value: 1 } } }] },
      'the API sent an incomplete applied usage price',
    ],
  ])('refuses %j', (body, message) => {
    expect(() => toRecord(body as Parameters<typeof toRecord>[0])).toThrow(message);
  });
});

describe('toMessage', () => {
  const message = { id: 'm1', state: 'completed', messageType: 'SMS' };

  it.each([
    [{ ...message, id: undefined }, 'the API sent a message without an id or a state'],
    [{ ...message, state: undefined }, 'the API sent a message without an id or a state'],
    [{ ...message, messageType: 'Fax' }, 'the API sent a message of an unknown type Fax'],
    [{ ...message, messageType: undefined }, 'the API sent a message of an unknown type undefined'],
  ])('refuses %j', (body, error) => {
    expect(() => toMessage(body as Parameters<typeof toMessage>[0])).toThrow(error);
  });
});

describe('addressErrors', () => {
  it("leaves the problems that name no address input for the form's footer", () => {
    const error = new ApiError(
      'PATCH',
      '/x',
      422,
      'Unprocessable Entity',
      'contact.0.contactMedium.0: postCode must be a ZIP code; contact.0.contactMedium.0: country must be US;  ; contactMedium is required',
    );
    expect(addressErrors(error)).toEqual({
      fields: { postCode: 'postCode must be a ZIP code' },
      rest: ['contact.0.contactMedium.0: country must be US', 'contactMedium is required'],
    });
  });

  it('maps nothing from an error that is not a 422', () => {
    expect(addressErrors(new Error('Network error'))).toEqual({ fields: {}, rest: [] });
  });
});

describe('probe', () => {
  it('reports an unwell endpoint with its body and one that sends no JSON without one', async () => {
    stubFetch({
      'GET /healthz': () => json({ db: 'down' }, { status: 503 }),
      'GET /metrics': () => new Response('# HELP up', { status: 200 }),
    });
    expect(await probe('/healthz', signal())).toEqual({
      ok: false,
      status: 503,
      body: { db: 'down' },
    });
    expect(await probe('/metrics', signal())).toEqual({ ok: true, status: 200, body: null });
  });
});

describe('billingStatusValue', () => {
  it.each([
    [59, '0 min ago'],
    [3600, '1 h ago'],
    [47 * 3600, '47 h ago'],
    [48 * 3600, '2 days ago'],
    [24 * 3600 * 9 + 5, '9 days ago'],
  ])('shows an age of %i s as %s', (seconds, text) => {
    expect(billingStatusValue('ageSeconds', seconds)).toBe(text);
  });
});

describe('lookups that find nothing', () => {
  const NUMBERS = '/tmf-api/resourceInventory/v5/resource';
  const SPEC = '/tmf-api/productCatalogManagement/v5/productSpecification';

  it('says whether a specification needs a number', async () => {
    stubFetch({
      [`GET ${SPEC}/s1`]: () => json({ resourceSpecification: [{ id: 'msisdn' }] }),
      [`GET ${SPEC}/s2`]: () => json({ resourceSpecification: [{ id: 'imsi' }] }),
    });
    expect(await requiresNumber('s1', signal())).toBe(true);
    expect(await requiresNumber('s2', signal())).toBe(false);
  });

  it('loads a gone number as null but passes on any other failure', async () => {
    stubFetch({
      [`GET ${NUMBERS}/n1`]: () => json({ id: 'n1', value: '15125550100' }),
      [`GET ${NUMBERS}/gone`]: () => tmfError(404, 'Not Found'),
      [`GET ${NUMBERS}/broken`]: () => tmfError(500, 'Internal Server Error'),
    });
    expect(await loadRealizingNumbers(['n1', 'gone', 'n1'], signal())).toEqual({
      n1: { id: 'n1', value: '15125550100' },
      gone: null,
    });
    const error = await loadRealizingNumbers(['n1', 'broken'], signal()).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 500 });
  });

  it.each(['terminated', 'cancelled', 'aborted'])(
    'finds no transfer receiver for a number only a %s product used',
    async (status) => {
      stubFetch({
        [`GET ${NUMBERS}?value=15125550100&offset=0&limit=100`]: () =>
          json([{ id: 'n1', value: '15125550100' }]),
        'GET /tmf-api/productInventory/v5/product?realizingResource.id=n1&offset=0&limit=100': () =>
          json([{ id: 'p1', status, billingAccount: { id: 'a1' } }]),
      });
      expect(await findReceiver('15125550100', signal())).toEqual({
        found: false,
        why: '15125550100 is not used by any subscriber.',
      });
    },
  );

  describe('a sent transfer whose receiving bucket cannot be read', () => {
    const HISTORY = '/tmf-api/prepayBalanceManagement/v4/balanceActionHistory';
    const transfer = {
      id: 't1',
      '@type': 'TransferBalance',
      status: 'confirmed',
      amount: { amount: 5, units: 'USD' },
      requestedDate: '2026-11-20T10:00:00Z',
      partyAccount: { id: 'a1', name: 'Sender' },
      receiverBucket: { id: 'b2' },
    };
    const routes = (bucket: () => Response | Promise<Response>) => ({
      [`GET ${HISTORY}?partyAccount.id=a1&offset=0&limit=100`]: () => json([transfer]),
      [`GET ${HISTORY}?receiverBucket.id=b1&offset=0&limit=100`]: () => json([]),
      'GET /tmf-api/prepayBalanceManagement/v4/bucket/b2': bucket,
    });

    it('shows the transfer without a receiver', async () => {
      stubFetch(routes(() => tmfError(404, 'Not Found')));
      const [action] = await loadBalanceActions('a1', 'b1', signal());
      expect(action).toMatchObject({ id: 't1', sent: true, cents: -500, receiver: null });
    });

    it('stops when the read is aborted', async () => {
      const controller = new AbortController();
      stubFetch(
        routes(() => {
          controller.abort();
          return tmfError(503, 'Service Unavailable');
        }),
      );
      const error = await loadBalanceActions('a1', 'b1', controller.signal).catch(
        (e: unknown) => e,
      );
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 503 });
    });
  });
});
