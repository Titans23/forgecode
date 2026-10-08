import React, { useRef, useState } from 'react';
import { submitsOnEnter } from '../../state/messages';
import { Icon } from '../../components/Icon';

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
      placeholder="描述你想完成的任务…"
      onChange={event => setText(event.target.value)}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => { if (submitsOnEnter(event.nativeEvent, composing.current)) { event.preventDefault(); void send(); } }}/>
    <div className="composer-bottom"><span>Enter 发送 · Shift + Enter 换行</span>
      <button disabled={disabled || !text.trim()} type="submit" aria-label="发送任务" title="发送任务"><Icon name="send" size={18}/></button></div>
  </form>;
}
