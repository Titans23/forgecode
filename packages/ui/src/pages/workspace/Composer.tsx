import React, { useRef, useState } from 'react';
import { submitsOnEnter } from '../../state/messages';
import { Icon } from '../../components/Icon';

export function Composer({ disabled, disabledReason, submit }: { disabled: boolean; disabledReason?: string; submit(text: string): Promise<void> }) {
  const [text, setText] = useState('');
  const composing = useRef(false), pending = useRef(false);
  async function send() {
    if (disabled || composing.current || pending.current || !text.trim()) return;
    pending.current = true;
    const value = text;
    setText('');
    try { await submit(value); } finally { pending.current = false; }
  }
  return <form className="composer" onSubmit={event => { event.preventDefault(); void send(); }}>
    <textarea aria-label="任务输入" aria-describedby="composer-help" maxLength={16384} value={text} disabled={disabled}
      placeholder="描述任务…"
      onChange={event => setText(event.target.value)}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => { if (submitsOnEnter(event.nativeEvent, composing.current)) { event.preventDefault(); void send(); } }}/>
    <div className="composer-bottom">{disabled && disabledReason ? <span id="composer-help" className="composer-hint" role="status">{disabledReason}</span> :
      <div id="composer-help" className="composer-shortcuts"><span><kbd>Enter</kbd>发送</span><span><kbd>Shift</kbd><span>+</span><kbd>Enter</kbd>换行</span></div>}
      <button disabled={disabled || !text.trim()} type="submit" aria-label="发送任务" title={disabled && disabledReason ? disabledReason : text.trim() ? '发送任务' : '输入任务后发送'}><Icon name="send" size={18}/></button></div>
  </form>;
}
