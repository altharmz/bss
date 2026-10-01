import { useSetCrumbs } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { EmptyBlock } from '../ui/states';
import { Panel } from '../ui/kit';

// What the router shows for a path that has no screen (including /billing until stage 4).

export function NotFoundScreen({ path }: { path: string }) {
  useSetCrumbs([{ label: 'Not found' }], `notFound:${path}`);
  return (
    <Panel>
      <EmptyBlock
        title="Page not found"
        text={`Nothing lives at ${path}.`}
        action={<Link href={hrefOf({ name: 'subscribers' })}>Go to subscribers</Link>}
      />
    </Panel>
  );
}
