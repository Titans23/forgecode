/** UI code depends on named business operations; Electron stays in Main/Preload. */
import type * as EvaluationContract from '@forgecode/contracts';
export interface NativeFileResult { cancelled:boolean; saved?:boolean; template_id?:string; run_ids?:string[]; origin?:string }
import type { SessionListRequest, SessionListResult, SessionSnapshotRequest, SessionSnapshotResult,
  SessionCreateDefaultRequest, SessionCreateDefaultResult, SessionSubmitRequest, SessionSubmitResult,
  WorkspaceFilesRequest, WorkspaceFilesResult, WorkspaceReadFileRequest, WorkspaceReadFileResult,
  WorkspaceChangesRequest, WorkspaceChangesResult, WorkspaceDiffRequest, WorkspaceDiffResult,
  WorkspaceDiffFileRequest, WorkspaceDiffFileResult, SystemHealthResult,
  ObservabilitySpansRequest, ObservabilitySpansResult, ObservabilityContextRequest, ObservabilityContextResult,
  ObservabilityEvidenceRequest, ObservabilityEvidenceResult, ObservabilityUsageRequest, ObservabilityUsageResult,
  ObservabilityEventsRequest, ObservabilityEventsResult, ObservabilityOutputRequest, ObservabilityOutputResult,
  ObservabilityTimingsRequest, ObservabilityTimingsResult, ArtifactReadChunkRequest, ArtifactReadChunkResult } from '@forgecode/contracts';
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
  recoveryInspect(value: EvaluationContract.RecoveryInspectRequest): Promise<EvaluationContract.RecoveryInspectResult>;
  createSession(value: SessionCreateDefaultRequest): Promise<SessionCreateDefaultResult>;
  submit(value: SessionSubmitRequest): Promise<SessionSubmitResult>;
  files(value: WorkspaceFilesRequest): Promise<WorkspaceFilesResult>;
  readProjectFile(value: WorkspaceReadFileRequest): Promise<WorkspaceReadFileResult>;
  changes(value: WorkspaceChangesRequest): Promise<WorkspaceChangesResult>;
  diff(value: WorkspaceDiffRequest): Promise<WorkspaceDiffResult>;
  diffFile(value: WorkspaceDiffFileRequest): Promise<WorkspaceDiffFileResult>;
  observationSpans(value: ObservabilitySpansRequest): Promise<ObservabilitySpansResult>;
  observationContext(value: ObservabilityContextRequest): Promise<ObservabilityContextResult>;
  observationEvidence(value: ObservabilityEvidenceRequest): Promise<ObservabilityEvidenceResult>;
  observationUsage(value: ObservabilityUsageRequest): Promise<ObservabilityUsageResult>;
  observationEvents(value: ObservabilityEventsRequest): Promise<ObservabilityEventsResult>;
  observationOutput(value: ObservabilityOutputRequest): Promise<ObservabilityOutputResult>;
  observationTimings(value: ObservabilityTimingsRequest): Promise<ObservabilityTimingsResult>;
  artifactChunk(value: ArtifactReadChunkRequest): Promise<ArtifactReadChunkResult>;
  evaluationTemplates(): Promise<EvaluationContract.EvaluationTemplatesResult>;
  evaluationTemplate(value: EvaluationContract.EvaluationTemplateRequest): Promise<EvaluationContract.EvaluationTemplateResult>;
  evaluationDraft(value: EvaluationContract.EvaluationDraftRequest): Promise<EvaluationContract.EvaluationDraftResult>;
  evaluationValidate(value: EvaluationContract.EvaluationValidateRequest): Promise<EvaluationContract.EvaluationValidateResult>;
  evaluationCreate(value: EvaluationContract.EvaluationCreateRunRequest): Promise<EvaluationContract.EvaluationCreateRunResult>;
  evaluationStart(value: EvaluationContract.EvaluationStartRequest): Promise<EvaluationContract.EvaluationStartResult>;
  evaluationCancel(value: EvaluationContract.EvaluationCancelRequest): Promise<EvaluationContract.EvaluationCancelResult>;
  evaluationRetry(value: EvaluationContract.EvaluationRetryRequest): Promise<EvaluationContract.EvaluationRetryResult>;
  evaluationRuns(value: EvaluationContract.EvaluationListRequest): Promise<EvaluationContract.EvaluationListResult>;
  evaluationSnapshot(value: EvaluationContract.EvaluationSnapshotRequest): Promise<EvaluationContract.EvaluationSnapshotResult>;
  evaluationComparison(value: EvaluationContract.EvaluationComparisonRequest): Promise<EvaluationContract.EvaluationComparisonResult>;
  failureList(value: EvaluationContract.FailureListRequest): Promise<EvaluationContract.FailureListResult>;
  failureGet(value: EvaluationContract.FailureGetRequest): Promise<EvaluationContract.FailureGetResult>;
  failureAnnotate(value: EvaluationContract.FailureAnnotateRequest): Promise<EvaluationContract.FailureAnnotateResult>;
  failureSaveCandidate(value: EvaluationContract.FailureSaveCandidateRequest): Promise<EvaluationContract.FailureSaveCandidateResult>;
  failureCheckReproduction(value: EvaluationContract.FailureCheckReproductionRequest): Promise<EvaluationContract.FailureCheckReproductionResult>;
  failureCandidate(value: EvaluationContract.FailureCandidateRequest): Promise<EvaluationContract.FailureCandidateResult>;
  exportRegressionCandidate(id:string): Promise<NativeFileResult>;
  importExperimentPlan(): Promise<NativeFileResult>;
  exportExperimentPlan(runId:string): Promise<NativeFileResult>;
  importResults(): Promise<NativeFileResult>;
  exportResults(runId:string): Promise<NativeFileResult>;
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
  recoveryInspect(value: EvaluationContract.RecoveryInspectRequest) { return this.bridge().recoveryInspect(value); }
  createSession(value: SessionCreateDefaultRequest) { return this.bridge().createSession(value); }
  submit(value: SessionSubmitRequest) { return this.bridge().submit(value); }
  files(value: WorkspaceFilesRequest) { return this.bridge().files(value); }
  readProjectFile(value: WorkspaceReadFileRequest) { return this.bridge().readProjectFile(value); }
  changes(value: WorkspaceChangesRequest) { return this.bridge().changes(value); }
  diff(value: WorkspaceDiffRequest) { return this.bridge().diff(value); }
  diffFile(value: WorkspaceDiffFileRequest) { return this.bridge().diffFile(value); }
  observationSpans(value: ObservabilitySpansRequest) { return this.bridge().observationSpans(value); }
  observationContext(value: ObservabilityContextRequest) { return this.bridge().observationContext(value); }
  observationEvidence(value: ObservabilityEvidenceRequest) { return this.bridge().observationEvidence(value); }
  observationUsage(value: ObservabilityUsageRequest) { return this.bridge().observationUsage(value); }
  observationEvents(value: ObservabilityEventsRequest) { return this.bridge().observationEvents(value); }
  observationOutput(value: ObservabilityOutputRequest) { return this.bridge().observationOutput(value); }
  observationTimings(value: ObservabilityTimingsRequest) { return this.bridge().observationTimings(value); }
  artifactChunk(value: ArtifactReadChunkRequest) { return this.bridge().artifactChunk(value); }
  evaluationTemplates() { return this.bridge().evaluationTemplates(); }
  evaluationTemplate(value: EvaluationContract.EvaluationTemplateRequest) { return this.bridge().evaluationTemplate(value); }
  evaluationDraft(value: EvaluationContract.EvaluationDraftRequest) { return this.bridge().evaluationDraft(value); }
  evaluationValidate(value: EvaluationContract.EvaluationValidateRequest) { return this.bridge().evaluationValidate(value); }
  evaluationCreate(value: EvaluationContract.EvaluationCreateRunRequest) { return this.bridge().evaluationCreate(value); }
  evaluationStart(value: EvaluationContract.EvaluationStartRequest) { return this.bridge().evaluationStart(value); }
  evaluationCancel(value: EvaluationContract.EvaluationCancelRequest) { return this.bridge().evaluationCancel(value); }
  evaluationRetry(value: EvaluationContract.EvaluationRetryRequest) { return this.bridge().evaluationRetry(value); }
  evaluationRuns(value: EvaluationContract.EvaluationListRequest) { return this.bridge().evaluationRuns(value); }
  evaluationSnapshot(value: EvaluationContract.EvaluationSnapshotRequest) { return this.bridge().evaluationSnapshot(value); }
  evaluationComparison(value: EvaluationContract.EvaluationComparisonRequest) { return this.bridge().evaluationComparison(value); }
  failureList(value: EvaluationContract.FailureListRequest) { return this.bridge().failureList(value); }
  failureGet(value: EvaluationContract.FailureGetRequest) { return this.bridge().failureGet(value); }
  failureAnnotate(value: EvaluationContract.FailureAnnotateRequest) { return this.bridge().failureAnnotate(value); }
  failureSaveCandidate(value: EvaluationContract.FailureSaveCandidateRequest) { return this.bridge().failureSaveCandidate(value); }
  failureCheckReproduction(value: EvaluationContract.FailureCheckReproductionRequest) { return this.bridge().failureCheckReproduction(value); }
  failureCandidate(value: EvaluationContract.FailureCandidateRequest) { return this.bridge().failureCandidate(value); }
  exportRegressionCandidate(id:string) { return this.bridge().exportRegressionCandidate(id); }
  importExperimentPlan() { return this.bridge().importExperimentPlan(); }
  exportExperimentPlan(runId:string) { return this.bridge().exportExperimentPlan(runId); }
  importResults() { return this.bridge().importResults(); }
  exportResults(runId:string) { return this.bridge().exportResults(runId); }
  diagnostics() { return this.bridge().diagnostics(); }
  diagnoseSandbox() { return this.bridge().diagnoseSandbox(); }
  installSandbox() { return this.bridge().installSandbox(); }
  startDemo() { return this.bridge().startDemo(); }
  cancelTurn(id: string) { return this.bridge().cancelTurn(id); }
  events() { return this.bridge().events(); }
}
