import React, { useEffect, useState } from 'react';
import type { DesktopOperations, ConnectionForm, ConnectionMetadata, ConnectionPage } from '../../transport';
import { Icon } from '../../components/Icon';

const emptyForm: ConnectionForm = { provider: 'anthropic', base_url: '', requested_model: '', credential: '' };
export function Connections({ transport }: { transport: DesktopOperations }) {
  const [page, setPage] = useState<ConnectionPage | null>(null);
  const [form, setForm] = useState<ConnectionForm>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  useEffect(() => { let active = true; transport.connections().then(value => { if (active) setPage(value); })
    .catch(reason => { if (active) setError(reason.message); }); return () => { active = false; }; }, [transport]);
  async function action(operation: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true); setError(''); setResult('');
    try { await operation(); setPage(await transport.connections()); }
    catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  function edit(row: ConnectionMetadata) {
    setForm({ connection_id: row.connection_id, expected_revision: row.revision, provider: row.provider,
      base_url: row.base_url, requested_model: row.requested_model, credential: '' });
  }
  function save(event: React.FormEvent) {
    event.preventDefault();
    const submitted = { ...form };
    setForm(value => ({ ...value, credential: '' }));
    void action(async () => {
      try { await transport.saveConnection(submitted); } finally { submitted.credential = ''; }
    });
  }
  const protection = page?.protection;
  return <div className="settings"><section><div className="page-intro"><span className="intro-icon"><Icon name="link" size={23}/></span>
    <h2>模型连接</h2></div>
    <p className="protection-note" data-testid="credential-protection" data-warning={!!protection && (protection.mode !== 'os_protected' || protection.state === 'temporarily_unavailable')}
      title={protection ? `密钥存储方式：${protection.backend}` : undefined}><Icon name="shield" size={14}/>{!protection ? '正在检查密钥保护' : protection.state === 'temporarily_unavailable' ? '系统密钥存储暂不可用' :
      protection.mode === 'os_protected' ? '密钥由当前系统用户保护' : '密钥仅保留在本次客户端运行期间'}</p>
    {error && <div role="alert" className="notice">{error}</div>}{result && <div role="status">{result}</div>}
    <form onSubmit={save} autoComplete="off" className="connection-form">
      <div className="form-grid"><label className="field">服务商<select value={form.provider} disabled={busy} onChange={event => setForm(value => ({ ...value, provider: event.target.value }))}>
        <option value="anthropic">Anthropic</option><option value="openai_responses">OpenAI Responses</option><option value="deepseek">DeepSeek</option></select></label>
      <label className="field">模型名称<input required placeholder="服务商提供的模型 ID" value={form.requested_model} disabled={busy} onChange={event => setForm(value => ({ ...value, requested_model: event.target.value }))}/></label>
      <label className="field field-wide">服务地址<input required type="url" placeholder="https://api.example.com" value={form.base_url} disabled={busy}
        onChange={event => setForm(value => ({ ...value, base_url: event.target.value }))}/></label>
      <label className="field field-wide">API 密钥<input type="password" autoComplete="off" aria-describedby={form.connection_id ? 'credential-hint' : undefined} maxLength={16384} value={form.credential} disabled={busy}
        onChange={event => setForm(value => ({ ...value, credential: event.target.value }))}/></label></div>
      {form.connection_id && <p className="field-hint" id="credential-hint">请重新填写密钥；留空保存会移除原密钥。</p>}
      <div className="actions"><button disabled={busy}><Icon name="check" size={16}/>{busy ? '正在处理…' : '保存连接'}</button><button type="button" className="secondary" disabled={busy} onClick={() => setForm(emptyForm)}><Icon name="plus" size={16}/>新建连接</button></div>
      <p className="connection-help">保存和测试需系统确认。</p>
    </form></section>
    {page?.items.map(row => <section className="connection-card" key={row.connection_id}><div className="panel-heading"><div><h3>{row.requested_model}</h3><p>{row.provider} · {row.base_url}</p></div>
      <span className="status-tag"><Icon name={row.locked ? 'lock' : row.credential_present ? 'check' : 'issue'} size={13}/>{row.locked ? '暂时锁定' : row.credential_present ? '密钥可用' : '缺少密钥'}</span></div>
      <div className="actions"><button className="secondary" disabled={busy} onClick={() => edit(row)}><Icon name="compose" size={15}/>编辑</button>
        <button className="secondary" title="确认后查询该地址的模型列表" disabled={busy || !row.credential_present} onClick={() => action(async () => {
          const value = await transport.testConnection(row.connection_id); setResult(value.cancelled ? '已取消测试' : value.status === 'pass' ? '已成功获取模型列表' : '模型列表请求未完成');
        })}><Icon name="activity" size={15}/>测试连接</button>
        <button className="secondary" title="只影响本次客户端运行，可随时解锁" disabled={busy} onClick={() => action(() => row.locked ? transport.unlockConnection(row.connection_id) : transport.lockConnection(row.connection_id))}><Icon name={row.locked ? 'unlock' : 'lock'} size={15}/>{row.locked ? '解锁' : '临时锁定'}</button>
        <button className="secondary danger-action" disabled={busy} onClick={() => action(() => transport.deleteConnection(row.connection_id))}><Icon name="trash" size={15}/>删除</button></div>
    </section>)}
  </div>;
}
