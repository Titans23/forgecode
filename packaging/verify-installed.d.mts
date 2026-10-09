export function verifyInstalled(root:string,options?:{target?:string;contractHash?:string;production?:boolean}):Promise<any>;
export function verifyToolBundle(root:string,bundle:any):Promise<string>;
export function verifyToolFiles(directory:string,entries:any[]):Promise<void>;
