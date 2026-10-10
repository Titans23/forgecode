import { StringDecoder } from 'node:string_decoder';

/** Only this envelope crosses the control channel; task bytes are never parsed as RPC. */
export class OutputCapture {
  stdoutBytes = 0;
  stderrBytes = 0;
  discardedBytes = 0;
  private retained = 0;
  private sequence = 0;
  private decoders = { stdout: new StringDecoder('utf8'), stderr: new StringDecoder('utf8') };
  constructor(private limit: number, private owner: Record<string, unknown>,
    private emit: (value: Record<string, unknown>) => boolean) {}

  feed(stream: 'stdout' | 'stderr', bytes: Buffer): void {
    if (stream === 'stdout') this.stdoutBytes += bytes.length;
    else this.stderrBytes += bytes.length;
    for (let offset = 0; offset < bytes.length; offset += 8192) {
      const raw = bytes.subarray(offset, Math.min(bytes.length, offset + 8192, offset + Math.max(0, this.limit - this.retained)));
      const attempted = Math.min(8192, bytes.length - offset);
      if (raw.length) {
        const text = this.decoders[stream].write(raw);
        const accepted = this.emit({ execution_id: this.owner.execution_id, owner: this.owner,
          stream, sequence: String(++this.sequence), raw_base64: raw.toString('base64'), text,
          encoding: 'utf-8', final: false });
        this.retained += raw.length;
        if (!accepted) {
          this.discardedBytes += raw.length;
          this.decoders[stream] = new StringDecoder('utf8');
        }
      }
      this.discardedBytes += attempted - raw.length;
    }
  }

  end(): void {
    for (const stream of ['stdout', 'stderr'] as const) {
      const text = this.decoders[stream].end();
      this.emit({ execution_id: this.owner.execution_id, owner: this.owner, stream,
        sequence: String(++this.sequence), raw_base64: '', text, encoding: 'utf-8', final: true });
    }
  }
}
