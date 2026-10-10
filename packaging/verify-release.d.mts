export type Asset = { path: string; sha256: string };
export function verifyAsset(root: string, asset: Asset): Promise<string>;
export function verifyReleaseLock(root: string, target?: string, release?: boolean): Promise<any>;
