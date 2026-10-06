/** Sandboxed preload exposes named business operations, never raw IPC or file/command APIs. */
import { contextBridge, ipcRenderer } from 'electron';
contextBridge.exposeInMainWorld('forgeDesktop', Object.freeze({
  security: () => ({ contextIsolated: process.contextIsolated, sandboxed: process.sandboxed }),
  status: () => ipcRenderer.invoke('forge:status'),
  projects: () => ipcRenderer.invoke('forge:projects'),
  selectProject: () => ipcRenderer.invoke('forge:select-project'),
  authorizeWorkspace: (workspaceId: string) => ipcRenderer.invoke('forge:authorize-workspace', { workspace_id: workspaceId }),
  approvals: () => ipcRenderer.invoke('forge:approvals'),
  requestApproval: (approvalId: string) => ipcRenderer.invoke('forge:request-approval', { approval_id: approvalId }),
  connections: () => ipcRenderer.invoke('forge:connections'),
  saveConnection: (value: unknown) => ipcRenderer.invoke('forge:save-connection', value),
  deleteConnection: (id: string) => ipcRenderer.invoke('forge:delete-connection', { connection_id: id }),
  lockConnection: (id: string) => ipcRenderer.invoke('forge:lock-connection', { connection_id: id }),
  unlockConnection: (id: string) => ipcRenderer.invoke('forge:unlock-connection', { connection_id: id }),
  testConnection: (id: string) => ipcRenderer.invoke('forge:test-connection', { connection_id: id }),
  session: (sessionId: string) => ipcRenderer.invoke('forge:session', { session_id: sessionId }),
  startDemo: () => ipcRenderer.invoke('forge:start-demo'),
  cancelTurn: (turnId: string) => ipcRenderer.invoke('forge:cancel-turn', { turn_id: turnId }),
  events: () => ipcRenderer.invoke('forge:events')
}));
