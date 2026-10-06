/** UI code depends on named business operations; Electron stays in Main/Preload. */
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
export interface DesktopOperations {
  status(): Promise<DesktopStatus>;
  projects(): Promise<{ items: Array<{ workspace_id: string; name?: string; [key: string]: unknown }> }>;
  selectProject(): Promise<unknown>;
  authorizeWorkspace(id: string): Promise<unknown>;
  approvals(): Promise<{ items: Array<{ approval_id: string; state: string; tool_name?: string; risk?: string }> }>;
  requestApproval(id: string): Promise<unknown>;
  session(id: string): Promise<SessionSnapshot>;
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
  session(id: string) { return this.bridge().session(id); }
  startDemo() { return this.bridge().startDemo(); }
  cancelTurn(id: string) { return this.bridge().cancelTurn(id); }
  events() { return this.bridge().events(); }
}
