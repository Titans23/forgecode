/** Every named operation authenticates the current window and its main frame. */
import type { BrowserWindow, IpcMainInvokeEvent } from 'electron';
import { validate } from '@forgecode/contracts';

export function assertSender(current: BrowserWindow | null, event: IpcMainInvokeEvent): void {
  if (!current || current.isDestroyed() || event.sender !== current.webContents ||
      event.senderFrame !== current.webContents.mainFrame || event.senderFrame.url !== 'forge-app://ui/index.html') {
    throw new Error('IPC sender is not the current main UI frame');
  }
}
export function empty(value: unknown): void {
  if (value !== undefined) throw new Error('Operation accepts no payload');
}
export function businessId(value: unknown, key: string, schema: string): string {
  validate(schema, value);
  return (value as Record<string, string>)[key];
}
export function captureSender(getWindow: () => BrowserWindow | null, event: IpcMainInvokeEvent): () => BrowserWindow {
  const window = getWindow();
  assertSender(window, event);
  const frame = event.senderFrame;
  return () => {
    if (getWindow() !== window || event.senderFrame !== frame) throw new Error('The requesting window changed');
    assertSender(window, event);
    return window!;
  };
}
