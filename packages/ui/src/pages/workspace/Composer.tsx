import React, { useRef, useState } from 'react';
import { submitsOnEnter } from '../../state/messages';

export function Composer({ disabled, submit }: { disabled: boolean; submit(text: string): Promise<void> }) {
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
    <textarea aria-label="任务输入" maxLength={16384} value={text} disabled={disabled}
      placeholder="描述开发任务；Enter 发送，Shift+Enter 换行"
      onChange={event => setText(event.target.value)}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => { if (submitsOnEnter(event.nativeEvent, composing.current)) { event.preventDefault(); void send(); } }}/>
    <button disabled={disabled || !text.trim()} type="submit">发送任务</button>
  </form>;
}
