import { DialogFooter, submitDialogPrimary } from '../ui/kit';
import { useId, useState } from 'react';
import type { components } from '../api/generated/internal';
import { explain, getJson, postJson, request } from '../api/client';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { useSession } from '../app/session';
import { Button, Modal, PageTitle, Panel, TextInput } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { businessDate, clockTime } from '../format/dates';

type User = components['schemas']['User'];
const PATH = '/internal/v1/users';
const columns: Column[] = [
  { key: 'username', label: 'Username', width: '1fr' },
  { key: 'role', label: 'Role', width: '100px' },
  { key: 'active', label: 'Active', width: '80px' },
  { key: 'last', label: 'Last sign-in', width: '200px' },
  { key: 'actions', label: 'Actions', width: '250px' },
];

export function UsersScreen() {
  useSetCrumbs([{ label: 'Users' }], 'users');
  const session = useSession();
  if (session?.role !== 'admin')
    return (
      <Panel>
        <h1>Not allowed</h1>
        <p>Only administrators can manage users.</p>
      </Panel>
    );
  return (
    <div className="bss-screen" data-ui="ui.users.list">
      <UsersList />
    </div>
  );
}

function UsersList() {
  const session = useSession();
  const { timeZone } = useSystemContext();
  const users = useResource(PATH, (signal) => getJson<User[]>(PATH, signal));
  const [dialog, setDialog] = useState<{ kind: 'new' | 'edit' | 'reset'; user?: User } | null>(
    null,
  );
  return (
    <>
      <PageTitle title="Users" />
      <Panel
        title="Users"
        right={<Button onClick={() => setDialog({ kind: 'new' })}>New user</Button>}
      >
        <div role="table" aria-label="Users">
          <TableHead columns={columns} />
          <ListState
            resource={users}
            columns={columns}
            errorTitle="Failed to load users"
            empty={{ title: 'No users', text: 'Create a user to grant access.' }}
            total={(data) => data.length}
            rows={(data) => data}
            onResetFilters={() => {}}
          >
            {(rows) =>
              rows.map((user) => (
                <TableRow
                  key={user.username}
                  columns={columns}
                  cells={{
                    username: <span style={{ overflowWrap: 'anywhere' }}>{user.username}</span>,
                    role: user.role,
                    active: user.disabled ? 'No' : 'Yes',
                    last: user.lastLoginAt
                      ? `${businessDate(user.lastLoginAt, timeZone)} ${clockTime(user.lastLoginAt, timeZone)}`
                      : 'Never',
                    actions: (
                      <>
                        <Button
                          small
                          aria-label={`Edit ${user.username}`}
                          onClick={() => setDialog({ kind: 'edit', user })}
                        >
                          Edit
                        </Button>{' '}
                        <Button
                          small
                          aria-label={`Reset password for ${user.username}`}
                          onClick={() => setDialog({ kind: 'reset', user })}
                        >
                          Reset password
                        </Button>
                      </>
                    ),
                  }}
                />
              ))
            }
          </ListState>
        </div>
      </Panel>
      {dialog && (
        <UserForm
          kind={dialog.kind}
          user={dialog.user}
          self={dialog.user?.username === session?.username}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null);
            if (dialog.user?.username === session?.username)
              window.dispatchEvent(new Event('bss:unauthorized'));
            else users.retry();
          }}
        />
      )}
    </>
  );
}

function UserForm({
  kind,
  user,
  self,
  onClose,
  onSaved,
}: {
  kind: 'new' | 'edit' | 'reset';
  user?: User | undefined;
  self: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const formId = useId();
  const [username, setUsername] = useState(user?.username ?? '');
  const [role, setRole] = useState<'admin' | 'user'>(user?.role ?? 'user');
  const [disabled, setDisabled] = useState(user?.disabled ?? false);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const title =
    kind === 'new' ? 'New user' : `${kind === 'edit' ? 'Edit' : 'Reset password for'} ${username}`;
  return (
    <Modal
      onSubmit={submitDialogPrimary}
      ui="ui.users.manage"
      title={title}
      width={480}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <form
        id={formId}
        className="bss-auth-form"
        onSubmit={async (event) => {
          event.preventDefault();
          setError('');
          setBusy(true);
          try {
            if (kind === 'new') await postJson(PATH, { username, role, password });
            else {
              const body =
                kind === 'reset'
                  ? { password }
                  : {
                      ...(role !== user?.role ? { role } : {}),
                      ...(disabled !== user?.disabled ? { disabled } : {}),
                    };
              if (Object.keys(body).length === 0) {
                onClose();
                return;
              }
              await request('PATCH', `${PATH}/${encodeURIComponent(username)}`, {
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
              });
            }
            onSaved();
          } catch (e) {
            setError(explain(e));
            setPassword('');
          } finally {
            setBusy(false);
          }
        }}
      >
        {kind === 'new' && (
          <label>
            Username
            <TextInput
              required
              pattern={'[A-Za-z0-9._@\\-]{1,64}'}
              maxLength={64}
              autoComplete="off"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
        )}
        {kind !== 'reset' && (
          <label>
            Role
            <select value={role} onChange={(e) => setRole(e.target.value as 'admin' | 'user')}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </label>
        )}
        {kind === 'edit' && (
          <label>
            <input
              type="checkbox"
              checked={disabled}
              disabled={self}
              onChange={(e) => setDisabled(e.target.checked)}
            />
            Disabled{self && ' (you cannot disable yourself)'}
          </label>
        )}
        {kind !== 'edit' && (
          <>
            <p>
              At least 12 characters, different from the username. The user must change this
              password at sign-in.
            </p>
            <label>
              Initial password
              <TextInput
                required
                type="password"
                minLength={12}
                maxLength={1024}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          </>
        )}
        {self && <p>Saving changes revokes your session. Sign in again afterward.</p>}
        {error && <p role="alert">{error}</p>}
        <DialogFooter>
          <Button type="submit" form={formId} variant="primary" disabled={busy}>
            {kind === 'reset' ? 'Reset password' : 'Save'}
          </Button>
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
        </DialogFooter>
      </form>
    </Modal>
  );
}
