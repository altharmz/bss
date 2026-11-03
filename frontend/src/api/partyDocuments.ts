export interface ContactFields {
  email: string;
  phone: string;
  street1: string;
  city: string;
  postCode: string;
  country: string;
}

export const EMPTY_CONTACT: ContactFields = {
  email: '',
  phone: '',
  street1: '',
  city: '',
  postCode: '',
  country: '',
};

export interface ContactMediumBody {
  '@type'?: string;
  contactType?: string;
  emailAddress?: string;
  phoneNumber?: string;
  street1?: string;
  city?: string;
  postCode?: string;
  country?: string;
}

export function contactMedia(form: ContactFields): ContactMediumBody[] {
  const media: ContactMediumBody[] = [];
  if (form.email.trim())
    media.push({
      '@type': 'EmailContactMedium',
      contactType: 'email',
      emailAddress: form.email.trim(),
    });
  if (form.phone.trim())
    media.push({
      '@type': 'PhoneContactMedium',
      contactType: 'phone',
      phoneNumber: form.phone.trim(),
    });
  const postal = {
    street1: form.street1.trim(),
    city: form.city.trim(),
    postCode: form.postCode.trim(),
    country: form.country.trim(),
  };
  if (Object.values(postal).some(Boolean))
    media.push({
      '@type': 'GeographicAddressContactMedium',
      contactType: 'postalAddress',
      ...Object.fromEntries(Object.entries(postal).filter(([, value]) => value)),
    });
  return media;
}

export function contactFields(media: ContactMediumBody[] | undefined): ContactFields {
  const email = media?.find(
    (medium) => medium.contactType === 'email' || medium['@type'] === 'EmailContactMedium',
  );
  const phone = media?.find(
    (medium) => medium.contactType === 'phone' || medium['@type'] === 'PhoneContactMedium',
  );
  const postal = media?.find(
    (medium) =>
      medium.contactType === 'postalAddress' ||
      medium['@type'] === 'GeographicAddressContactMedium',
  );
  return {
    email: email?.emailAddress ?? '',
    phone: phone?.phoneNumber ?? '',
    street1: postal?.street1 ?? '',
    city: postal?.city ?? '',
    postCode: postal?.postCode ?? '',
    country: postal?.country ?? '',
  };
}
