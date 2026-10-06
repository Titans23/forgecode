/** UI code depends on named business operations; Electron stays in Main/Preload. */
import type { SessionListRequest, SessionListResult, SessionSnapshotRequest, SessionSnapshotResult,
  SessionCreateDefaultRequest, SessionCreateDefaultResult, SessionSubmitRequest, SessionSubmitResult,
  WorkspaceFilesRequest, WorkspaceFilesResult, WorkspaceReadFileRequest, WorkspaceReadFileResult,
  WorkspaceChangesRequest, WorkspaceChangesResult, WorkspaceDiffRequest, WorkspaceDiffResult,
  WorkspaceDiffFileRequest, WorkspaceDiffFileResult, SystemHealthResult } from '@forgecode/contracts';
export interface DesktopStatus {
  engine_state: string;
  readiness: { status: string; blockers?: unknown[] } | null;
  mode: string;
  session_id: string | null;
  failure: string | null;
}
export interface SessionSnapshot {
  session_id: string;
  turns: Array<{ turn_id: string; state: string; outcome: string | null; [key: string]: unknown }>;
  [key: string]: unknown;
}
export interface ConnectionMetadata {
  connection_id: string; revision: number; provider: string; base_url: string; requested_model: string;
  credential_present: boolean; locked: boolean;
}
export interface ConnectionForm {
  connection_id?: string; expected_revision?: number; provider: string; base_url: string; requested_model: string; credential: string;
}
export interface ConnectionPage {
  items: ConnectionMetadata[];
  protection: { mode: string; backend: string; available: boolean; state: string; stored_in_memory: number };
}
export interface DesktopOperations {
  status(): Promise<DesktopStatus>;
  projects(): Promise<{ items: Array<{ workspace_id: string; name?: string; [key: string]: unknown }> }>;
  selectProject(): Promise<unknown>;
  authorizeWorkspace(id: string): Promise<unknown>;
  approvals(): Promise<{ items: Array<{ approval_id: string; state: string; tool_name?: string; risk?: string }> }>;
  requestApproval(id: string): Promise<unknown>;
  connections(): Promise<ConnectionPage>;
  saveConnection(value: ConnectionForm): Promise<unknown>;
  deleteConnection(id: string): Promise<unknown>;
  lockConnection(id: string): Promise<unknown>;
  unlockConnection(id: string): Promise<unknown>;
  testConnection(id: string): Promise<{ status?: string; cancelled?: boolean }>;
  session(id: string): Promise<SessionSnapshot>;
  sessions(value: SessionListRequest): Promise<SessionListResult>;
  sessionSnapshot(value: SessionSnapshotRequest): Promise<SessionSnapshotResult>;
  createSession(value: SessionCreateDefaultRequest): Promise<SessionCreateDefaultResult>;
  submit(value: SessionSubmitRequest): Promise<SessionSubmitResult>;
  files(value: WorkspaceFilesRequest): Promise<WorkspaceFilesResult>;
  readProjectFile(value: WorkspaceReadFileRequest): Promise<WorkspaceReadFileResult>;
  changes(value: WorkspaceChangesRequest): Promise<WorkspaceChangesResult>;
  diff(value: WorkspaceDiffRequest): Promise<WorkspaceDiffResult>;
  diffFile(value: WorkspaceDiffFileRequest): Promise<WorkspaceDiffFileResult>;
  diagnostics(): Promise<SystemHealthResult>;
  diagnoseSandbox(): Promise<{ status: string; reason?: string }>;
  installSandbox(): Promise<{ status: string; reason?: string }>;
  startDemo(): Promise<{ turn_id: string }>;
  cancelTurn(id: string): Promise<unknown>;
  events(): Promise<{ events: Array<{ event_id: string; event_type: string; [key: string]: unknown }>; gap: boolean }>;
}
declare global { interface Window { forgeDesktop: DesktopOperations } }
export class DesktopTransport implements DesktopOperations {
  private bridge(): DesktopOperations {
    if (!window.forgeDesktop) throw new Error('桌面传输不可用；请通过 ForgeCode 客户端打开。');
    return window.forgeDesktop;
  }
  status() { return this.bridge().status(); }
  projects() { return this.bridge().projects(); }
  selectProject() { return this.bridge().selectProject(); }
  authorizeWorkspace(id: string) { return this.bridge().authorizeWorkspace(id); }
  approvals() { return this.bridge().approvals(); }
  requestApproval(id: string) { return this.bridge().requestApproval(id); }
  connections() { return this.bridge().connections(); }
  saveConnection(value: ConnectionForm) { return this.bridge().saveConnection(value); }
  deleteConnection(id: string) { return this.bridge().deleteConnection(id); }
  lockConnection(id: string) { return this.bridge().lockConnection(id); }
  unlockConnection(id: string) { return this.bridge().unlockConnection(id); }
  testConnection(id: string) { return this.bridge().testConnection(id); }
  session(id: string) { return this.bridge().session(id); }
  sessions(value: SessionListRequest) { return this.bridge().sessions(value); }
  sessionSnapshot(value: SessionSnapshotRequest) { return this.bridge().sessionSnapshot(value); }
  createSession(value: SessionCreateDefaultRequest) { return this.bridge().createSession(value); }
  submit(value: SessionSubmitRequest) { return this.bridge().submit(value); }
  files(value: WorkspaceFilesRequest) { return this.bridge().files(value); }
  readProjectFile(value: WorkspaceReadFileRequest) { return this.bridge().readProjectFile(value); }
  changes(value: WorkspaceChangesRequest) { return this.bridge().changes(value); }
  diff(value: WorkspaceDiffRequest) { return this.bridge().diff(value); }
  diffFile(value: WorkspaceDiffFileRequest) { return this.bridge().diffFile(value); }
  diagnostics() { return this.bridge().diagnostics(); }
  diagnoseSandbox() { return this.bridge().diagnoseSandbox(); }
  installSandbox() { return this.bridge().installSandbox(); }
  startDemo() { return this.bridge().startDemo(); }
  cancelTurn(id: string) { return this.bridge().cancelTurn(id); }
  events() { return this.bridge().events(); }
}
