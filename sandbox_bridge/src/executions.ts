import { ContractError, canonicalHash } from '@forgecode/contracts';
import type { ChildProcess } from 'node:child_process';
import { OutputCapture } from './output.js';
import type { LinuxExecutionOwner } from './linux-ownership.js';

export interface Execution {
  hash: string;
  owner: Record<string, any>;
  state: 'accepted' | 'running' | 'finished' | 'indeterminate';
  exitCode: number | null;
  output: OutputCapture;
  child?: ChildProcess;
  abort: AbortController;
  done: Promise<void>;
  resolve: () => void;
  launchSettled?: Promise<void>;
  timer?: NodeJS.Timeout;
  cancellation?: Promise<Record<string, any>>;
  linuxOwner?: LinuxExecutionOwner;
}

/** A duplicate never launches again, including a launch whose outcome became unknown. */
export class Executions {
  readonly values = new Map<string, Execution>();
  constructor(private owner: Record<string, any>, private emit: (value: Record<string, unknown>) => boolean) {}
  accept(id: string, command: any, hash: string): { execution: Execution; reused: boolean } {
    if (canonicalHash(command) !== hash) throw new ContractError('Command hash mismatch');
    const existing = this.values.get(id);
    if (existing) {
      if (existing.hash !== hash) throw new ContractError('Execution ID conflicts with original command', 'POLICY_DENIED', -32010);
      return { execution: existing, reused: true };
    }
    if (this.values.size >= 4096) throw new ContractError('Session execution inventory is full', 'SANDBOX_UNAVAILABLE', -32010);
    let resolve!: () => void;
    const owner = { ...this.owner, execution_id: id };
    const execution: Execution = { hash, owner, state: 'accepted', exitCode: null,
      output: new OutputCapture(command.output_limit_bytes, owner, this.emit),
      abort: new AbortController(), done: new Promise<void>(r => { resolve = r; }), resolve };
    this.values.set(id, execution);
    return { execution, reused: false };
  }
  get(id: string): Execution {
    const value = this.values.get(id);
    if (!value) throw new ContractError('Execution is not owned by this Bridge', 'NOT_FOUND', -32010);
    return value;
  }
  handle(id: string, reused = false): any {
    const e = this.get(id);
    return { execution_id: id, state: e.state, reused_existing_execution: reused, owner: e.owner };
  }
  status(id: string): any {
    const e = this.get(id);
    return { execution_id: id, state: e.state, exit_code: e.exitCode, stdout_bytes: e.output.stdoutBytes,
      stderr_bytes: e.output.stderrBytes, discarded_bytes: e.output.discardedBytes, owner: e.owner };
  }
}
