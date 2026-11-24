import { DialogFooter, submitDialogPrimary } from '../ui/kit';
import { useId, useState } from 'react';
import type { components } from '../api/generated/internal';
import { explain, getJson, postJson, request } from '../api/client';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { useSession } from '../app/session';
import { SelectField, Button, Checkbox, Dialog, PageHeader, TextInput } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { businessDate, clockTime } from '../format/dates';

type User = components['schemas']['User'];
type Role = User['role'];
const PATH = '/internal/v1/users';
// an `mcp` user logs in through the MCP connector only; admin and user through the UI only
const crossesMcp = (from: Role, to: Role) => (from === 'mcp') !== (to === 'mcp');
const columns: Column[] = [
  { key: 'username', label: 'Username', width: '1fr' },
  { key: 'role', label: 'Role', width: '140px' },
  { key: 'active', label: 'Active', width: '80px' },
  { key: 'last', label: 'Last sign-in', width: '200px' },
  { key: 'actions', label: 'Actions', width: '250px' },
];

export function UsersScreen() {
  useSetCrumbs([{ label: 'Users' }], 'users');
  const session = useSession();
  if (session?.role !== 'admin')
    return (
      <div className="bss-screen" data-ui="ui.users.denied">
        <PageHeader title="Not allowed" />
        <p className="bss-not-found">Only administrators can manage users.</p>
      </div>
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
      <PageHeader
        title="Users"
        actions={<Button onClick={() => setDialog({ kind: 'new' })}>New user</Button>}
      />
      <div role="table" aria-label="Users">
        <TableHead columns={columns} />
        <ListState
          resource={users}
          columns={columns}
          errorTitle="Failed to load users"
          empty={{ title: 'No users', text: 'Create a user to grant access.' }}
          count={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => {}}
        >
          {(rows) =>
            rows.map((user) => (
              <TableRow
                key={user.username}
                columns={columns}
                cells={{
                  username: <span className="wrap-anywhere">{user.username}</span>,
                  role: user.role === 'mcp' ? 'mcp (MCP only)' : user.role,
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
  const [role, setRole] = useState<Role>(user?.role ?? 'user');
  const [disabled, setDisabled] = useState(user?.disabled ?? false);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // a role change to or from mcp carries a new password: one password never serves both
  const newPassword = kind !== 'edit' || (user !== undefined && crossesMcp(user.role, role));
  const title =
    kind === 'new' ? 'New user' : `${kind === 'edit' ? 'Edit' : 'Reset password for'} ${username}`;
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      ui="ui.users.manage"
      title={title}
      size="M"
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
                      ...(newPassword ? { password } : {}),
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
            <SelectField value={role} onChange={(e) => setRole(e.target.value as Role)}>
              <option value="user">user</option>
              <option value="admin">admin</option>
              <option value="mcp">mcp (MCP only, no UI sign-in)</option>
            </SelectField>
          </label>
        )}
        {kind === 'edit' && (
          <Checkbox
            checked={disabled}
            disabled={self}
            onChange={(e) => setDisabled(e.target.checked)}
            label={`Disabled${self ? ' (you cannot disable yourself)' : ''}`}
          />
        )}
        {newPassword && (
          <>
            <p>
              At least 12 characters, different from the username.{' '}
              {role === 'mcp'
                ? 'An MCP user keeps this password: give it to whoever connects the MCP client.'
                : 'The user must change this password at sign-in.'}
              {kind === 'edit' && ' A role change to or from mcp needs a new password.'}
            </p>
            <label>
              {kind === 'edit' ? 'New password' : 'Initial password'}
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
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form={formId} variant="primary" disabled={busy}>
            {kind === 'reset' ? 'Reset password' : 'Save'}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
