import { describe, expect, it } from 'vitest';
import {
  characteristicFields,
  offeringChanges,
  offeringEditFields,
  shownPrice,
  specificationChanges,
  type CatalogPrice,
  type OfferingDetail,
  type Specification,
  type SpecificationFields,
} from './bss';
import { catalogChanges, classificationFields, type ProductCatalog } from './classification';

// The edit forms send only what changed (JSON Merge Patch). These pin the bodies for the
// cases the catalog screens' tests do not reach.
const ZONE = 'America/Chicago';

describe('shownPrice', () => {
  const price = (id: string, cents: number, from: string): CatalogPrice => ({
    id,
    cents,
    currency: 'USD',
    unitOfMeasure: null,
    from,
    to: null,
  });

  it('shows the earliest of several future prices when none applies yet', () => {
    const prices = [
      price('later', 3000, '2027-03-01T06:00:00Z'),
      price('sooner', 2500, '2027-01-01T06:00:00Z'),
      { ...price('euro', 100, '2026-12-01T06:00:00Z'), currency: 'EUR' },
    ];
    expect(shownPrice(prices, '2026-11-26T12:00:00Z', 'USD')).toEqual({
      cents: 2500,
      from: '2027-01-01T06:00:00Z',
      interval: 'month',
    });
  });
});

describe('offeringChanges', () => {
  const offering: OfferingDetail = {
    id: 'o1',
    name: 'Prepaid M',
    description: null,
    status: 'Active',
    isSellable: true,
    validFrom: null,
    validTo: null,
    code: null,
    identifiers: [],
    specification: null,
    categories: [],
    priceRefs: [],
    kind: 'plan',
    passDays: null,
    characteristics: [],
    valueUse: [
      { name: 'zone', productSpecCharacteristicValue: [{ value: 'domestic' }] },
      { name: 'voiceMinutes', productSpecCharacteristicValue: [{ value: 100 }] },
    ],
  };

  it('reads the narrowed profile and sends every narrowed value in profile order', () => {
    const form = offeringEditFields(offering, ZONE);
    expect(form.network).toEqual({ voiceMinutes: '100' });
    const changes = offeringChanges(
      offering,
      { ...form, network: { smsCount: '50', voiceMinutes: '', dataAllowanceMb: '2048' } },
      ZONE,
    );
    expect(changes).toEqual({
      prodSpecCharValueUse: [
        // not a profile name: kept as loaded
        { name: 'zone', productSpecCharacteristicValue: [{ value: 'domestic' }] },
        ...[
          ['dataAllowanceMb', 2048],
          ['smsCount', 50],
        ].map(([name, value]) => ({
          '@type': 'ProductSpecificationCharacteristicValueUse',
          name,
          valueType: 'integer',
          productSpecCharacteristicValue: [
            { '@type': 'IntegerCharacteristicValueSpecification', valueType: 'integer', value },
          ],
        })),
      ],
    });
  });
});

describe('specificationChanges', () => {
  const allowance = {
    '@type': 'CharacteristicSpecification',
    name: 'dataAllowanceMb',
    valueType: 'integer',
    configurable: false,
    extra: 'kept',
    characteristicValueSpecification: [
      { '@type': 'IntegerCharacteristicValueSpecification', value: 1024 },
      { '@type': 'IntegerCharacteristicValueSpecification', value: 2048 },
    ],
  };
  const spec: Specification = {
    id: 's1',
    name: 'Mobile',
    description: null,
    status: 'Active',
    brand: null,
    version: null,
    productNumber: null,
    updated: null,
    resourceSpecification: [],
    productSpecCharacteristic: [
      allowance,
      {
        '@type': 'CharacteristicSpecification',
        name: 'smsCount',
        valueType: 'integer',
        characteristicValueSpecification: [
          { '@type': 'IntegerCharacteristicValueSpecification', value: 100 },
        ],
      },
    ] as Specification['productSpecCharacteristic'],
  };

  it('keeps an unchanged profile value exactly as loaded and rewrites the changed one', () => {
    const form: SpecificationFields = {
      name: 'Mobile',
      description: '',
      brand: '',
      version: '',
      productNumber: '',
      lifecycleStatus: 'Active',
      requiresNumber: false,
      characteristics: characteristicFields(spec),
      // the same values, spaced differently
      network: { dataAllowanceMb: '1024,2048', smsCount: '200' },
    };
    const changes = specificationChanges(spec, form);
    expect(changes).toEqual({
      productSpecCharacteristic: [
        allowance,
        {
          '@type': 'CharacteristicSpecification',
          name: 'smsCount',
          valueType: 'integer',
          configurable: false,
          characteristicValueSpecification: [
            {
              '@type': 'IntegerCharacteristicValueSpecification',
              valueType: 'integer',
              isDefault: true,
              value: 200,
            },
          ],
        },
      ],
    });
    expect((changes?.productSpecCharacteristic as unknown[])[0]).toBe(allowance);
  });
});

describe('catalogChanges', () => {
  const catalog: ProductCatalog = {
    id: 'c1',
    name: 'Retail',
    description: 'Shop offers',
    type: null,
    status: 'Active',
    categoryIds: [],
    validFrom: '2026-01-01T06:00:00Z',
    validTo: null,
  };

  it('clears a removed description, keeps a loaded start and adds the end', () => {
    const form = classificationFields(catalog, ZONE);
    expect(
      catalogChanges(catalog, { ...form, description: '  ', validTo: '2026-12-31' }, ZONE),
    ).toEqual({
      description: null,
      // the start as loaded; the end the business-zone midnight after the last valid day
      validFor: {
        startDateTime: '2026-01-01T06:00:00Z',
        endDateTime: '2027-01-01T00:00:00-06:00',
      },
    });
  });

  it('drops the validity when both dates are cleared and trims a new description', () => {
    const form = classificationFields(catalog, ZONE);
    expect(
      catalogChanges(catalog, { ...form, description: ' Web offers ', validFrom: '' }, ZONE),
    ).toEqual({ description: 'Web offers', validFor: null });
  });
});
