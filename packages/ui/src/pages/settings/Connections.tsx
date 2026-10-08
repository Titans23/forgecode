import React, { useEffect, useState } from 'react';
import type { DesktopOperations, ConnectionForm, ConnectionMetadata, ConnectionPage } from '../../transport';

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
  return <div className="settings"><section><h2>模型连接</h2>
    <p className="protection-note" data-testid="credential-protection">凭证保护：{!protection ? '正在读取' : protection.state === 'temporarily_unavailable' ? '系统存储暂不可用' :
      protection.mode === 'os_protected' ? '当前系统用户保护' : '仅本次应用内存'}{protection && ` · ${protection.backend}`}</p>
    <p>保存和更换地址需要系统确认。测试连接会另行确认，并向所选地址发送模型目录请求。</p>
    {error && <div role="alert" className="notice">{error}</div>}{result && <div role="status">{result}</div>}
    <form onSubmit={save} autoComplete="off" className="connection-form">
      <div className="form-grid"><label className="field">服务商<select value={form.provider} disabled={busy} onChange={event => setForm(value => ({ ...value, provider: event.target.value }))}>
        <option value="anthropic">Anthropic</option><option value="openai_responses">OpenAI Responses</option><option value="deepseek">DeepSeek</option></select></label>
      <label className="field">模型名称<input required placeholder="输入模型 ID" value={form.requested_model} disabled={busy} onChange={event => setForm(value => ({ ...value, requested_model: event.target.value }))}/></label>
      <label className="field field-wide">服务地址<input required type="url" placeholder="https://api.example.com" value={form.base_url} disabled={busy}
        onChange={event => setForm(value => ({ ...value, base_url: event.target.value }))}/></label>
      <label className="field field-wide">新凭证<input type="password" autoComplete="off" maxLength={16384} value={form.credential} disabled={busy}
        onChange={event => setForm(value => ({ ...value, credential: event.target.value }))}/></label></div>
      <div className="muted">编辑连接时重新输入凭证；留空会移除旧凭证。锁定仅影响本次应用会话。</div>
      <div className="actions"><button disabled={busy}>确认并保存</button><button type="button" className="secondary" disabled={busy} onClick={() => setForm(emptyForm)}>新建连接</button></div>
    </form></section>
    {page?.items.map(row => <section className="connection-card" key={row.connection_id}><div className="panel-heading"><div><h3>{row.requested_model}</h3><p>{row.provider} · {row.base_url}</p></div>
      <span className="status-tag">{row.locked ? '本次会话已锁定' : row.credential_present ? '凭证已就绪' : '需要输入凭证'}</span></div><div className="muted">版本 {row.revision}</div>
      <div className="actions"><button className="secondary" disabled={busy} onClick={() => edit(row)}>编辑</button>
        <button className="secondary" disabled={busy || !row.credential_present} onClick={() => action(async () => {
          const value = await transport.testConnection(row.connection_id); setResult(value.cancelled ? '已取消请求' : value.status === 'pass' ? '模型目录请求成功' : '模型目录请求失败或未完成');
        })}>确认并测试</button>
        <button className="secondary" disabled={busy} onClick={() => action(() => row.locked ? transport.unlockConnection(row.connection_id) : transport.lockConnection(row.connection_id))}>{row.locked ? '解锁' : '锁定本次会话'}</button>
        <button className="secondary danger-action" disabled={busy} onClick={() => action(() => transport.deleteConnection(row.connection_id))}>删除</button></div>
    </section>)}{page && !page.items.length && <p>尚未保存模型连接。</p>}
  </div>;
}
