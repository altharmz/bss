import { useSetCrumbs } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { EmptyBlock } from '../ui/states';
import { PageTitle, Panel } from '../ui/kit';

// Stand-ins until the real screens land (subscribers and the card in checkpoint 5, the
// catalog in checkpoint 6). They set the breadcrumbs the real screens will set.

function Pending({ title, checkpoint }: { title: string; checkpoint: number }) {
  return (
    <>
      <PageTitle title={title} />
      <Panel>
        <EmptyBlock
          title="Not built yet"
          text={`This screen arrives in checkpoint ${checkpoint}.`}
        />
      </Panel>
    </>
  );
}

export function CatalogScreen() {
  useSetCrumbs([{ label: 'Product catalog' }], 'catalog');
  return <Pending title="Product catalog" checkpoint={6} />;
}

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
