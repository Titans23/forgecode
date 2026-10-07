import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Ajv2020 } from 'ajv/dist/2020.js';
import formatsModule from 'ajv-formats';
import type { FormatsPlugin } from 'ajv-formats';
import bundle from './generated/schemas.json' with { type: 'json' };
export * from './core.js';
import {createValidators,canonicalString,ContractError,strictLoads} from './core.js';
import { ERROR_KINDS } from './generated/types.js';

const ajv=new Ajv2020({strict:false,allErrors:false,validateFormats:true,ownProperties:true});
(formatsModule as unknown as FormatsPlugin)(ajv);
for(const schema of Object.values(bundle.schemas))ajv.addSchema(schema);
export const {validate,validateRequest,validateEvent}=createValidators((name,value)=>{
  const validator=ajv.getSchema((bundle.schemas as Record<string,{$id:string}>)[name].$id)!;
  return validator(value)?null:validator.errors?.[0]??{keyword:'validation'};
});
export function canonicalHash(value: unknown): string {
  return createHash('sha256').update(canonicalString(value),'utf8').digest('hex');
}

// Test-only CLI invokes the same exported decoder and validators used by clients.
if (typeof import.meta.url === 'string' && process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1] && process.argv[2] === '--verify-fixtures') {
  const input = JSON.parse(readFileSync(0, 'utf8'));
  const valid = input.cases.map((item: { raw: string; schema: string | null }) => {
    try { const value = strictLoads(item.raw); if (item.schema) validate(item.schema, value); return true; }
    catch (error) { if (error instanceof ContractError) return false; throw error; }
  });
  const requests = (input.requests ?? []).map((item: { request: unknown; principal: 'main' | 'renderer' }) => {
    try { validateRequest(item.request, item.principal); return 'valid'; }
    catch (error) { if (error instanceof ContractError) return error.kind; throw error; }
  });
  process.stdout.write(JSON.stringify({ valid, requests, hash: canonicalHash(input.hash_value), error_kinds: ERROR_KINDS }));
}
