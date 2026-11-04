import { useSetCrumbs } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { PageHeader } from '../ui/kit';

// What the router shows for a path that has no screen (including /billing until stage 4).

export function NotFoundScreen({ path }: { path: string }) {
  useSetCrumbs([{ label: 'Not found' }], `notFound:${path}`);
  return (
    <div className="bss-screen" data-ui="ui.notFound">
      <PageHeader title="Page not found" />
      <p className="bss-not-found">
        Nothing lives at {path}.{' '}
        <Link href={hrefOf({ name: 'subscribers' })}>Go to Subscribers</Link>
      </p>
    </div>
  );
}
