/** Sandboxed preload exposes named business operations, never raw IPC or file/command APIs. */
import { contextBridge, ipcRenderer } from 'electron';
contextBridge.exposeInMainWorld('forgeDesktop', Object.freeze({
  security: () => ({ contextIsolated: process.contextIsolated, sandboxed: process.sandboxed }),
  status: () => ipcRenderer.invoke('forge:status'),
  projects: () => ipcRenderer.invoke('forge:projects'),
  session: (sessionId: string) => ipcRenderer.invoke('forge:session', { session_id: sessionId }),
  startDemo: () => ipcRenderer.invoke('forge:start-demo'),
  cancelTurn: (turnId: string) => ipcRenderer.invoke('forge:cancel-turn', { turn_id: turnId }),
  events: () => ipcRenderer.invoke('forge:events')
}));
