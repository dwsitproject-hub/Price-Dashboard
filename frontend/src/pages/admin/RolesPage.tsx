import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { AdminModule, Role } from '../../api/types';
import { ErrorAlert, useToast } from '../../components/Feedback';
import { Icon } from '../../components/Icon';

export function RolesPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const roles = useQuery({ queryKey: ['admin-roles'], queryFn: () => api.get<{ roles: Role[] }>('/admin/roles') });
  const modules = useQuery({ queryKey: ['admin-modules'], queryFn: () => api.get<{ modules: AdminModule[] }>('/admin/modules') });
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  useEffect(() => {
    if (roles.data) setDraft(Object.fromEntries(roles.data.roles.map((r) => [r.code, r.modules])));
  }, [roles.data]);

  const save = useMutation({
    mutationFn: (code: string) => api.put(`/admin/roles/${code}/modules`, { modules: draft[code] }),
    onSuccess: (_, code) => {
      qc.invalidateQueries({ queryKey: ['admin-roles'] });
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      toast(`Saved module access for ${code}`);
    },
  });

  const toggle = (code: string, mod: string) =>
    setDraft((d) => ({ ...d, [code]: d[code].includes(mod) ? d[code].filter((m) => m !== mod) : [...d[code], mod] }));
  const dirty = (r: Role) => JSON.stringify([...(draft[r.code] ?? [])].sort()) !== JSON.stringify([...r.modules].sort());

  return (
    <>
      <div className="page-head">
        <div><h1>Roles &amp; module access</h1>
          <p>Which analytics modules each role can open. Per-user overrides are set on the Users page. Enforced on the server for every request.</p></div>
      </div>
      <ErrorAlert error={roles.error || save.error} />
      <div className="grid-2" style={{ marginBottom: 16 }}>
        {roles.data?.roles.map((r) => (
          <div key={r.code} className="card card-pad">
            <div className="row"><b>{r.name}</b><span className="badge">{r.user_count} users</span>
              {r.permissions.map((p) => <span key={p} className="badge badge-brand">{p}</span>)}</div>
            <p className="muted" style={{ margin: '6px 0 0', fontSize: 12.5 }}>{r.description}</p>
          </div>
        ))}
      </div>
      <div className="card table-wrap">
        <table className="tbl matrix">
          <thead>
            <tr><th>Module</th>
              {roles.data?.roles.map((r) => <th key={r.code}>{r.name}</th>)}</tr>
          </thead>
          <tbody>
            {modules.data?.modules.map((m) => (
              <tr key={m.code}>
                <td><div style={{ fontWeight: 600 }}>{m.name}</div><div className="muted" style={{ fontSize: 12 }}>{m.description}</div></td>
                {roles.data?.roles.map((r) => {
                  const isAdmin = r.permissions.includes('admin');
                  return (
                    <td key={r.code}>
                      <input type="checkbox" className="checkbox" aria-label={`${r.name} can open ${m.name}`}
                             checked={isAdmin || (draft[r.code] ?? []).includes(m.code)} disabled={isAdmin}
                             onChange={() => toggle(r.code, m.code)} />
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <td className="muted">Administrator always has every module.</td>
              {roles.data?.roles.map((r) => (
                <td key={r.code}>
                  {!r.permissions.includes('admin') && (
                    <button className="btn btn-sm btn-primary" disabled={!dirty(r) || save.isPending} onClick={() => save.mutate(r.code)}>
                      <Icon name="check" size={14} /> Save
                    </button>
                  )}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}
