import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {validate as desktopValidate} from '../../../packages/contracts/dist/index.js';
const root=resolve(fileURLToPath(new URL('../../..',import.meta.url)));
const cases=[];
for(const name of ['method-fixtures.json','additional-fixtures.json']){
  cases.push(...JSON.parse(await readFile(resolve(root,'contracts/v1',name),'utf8')).cases);
}
const verdict=(validate,fixture)=>{
  try{validate(fixture.schema,fixture.value);return true;}catch{return false;}
};
const desktop=cases.map(fixture=>verdict(desktopValidate,fixture));
const originalFunction=globalThis.Function;
let dynamicEvaluations=0;
globalThis.Function=new Proxy(originalFunction,{apply(){dynamicEvaluations++;throw Error('CSP prohibits Function');},
  construct(){dynamicEvaluations++;throw Error('CSP prohibits Function');}});
try{
  const {validate}=await import('../../../packages/contracts/dist/browser.js');
  const browser=cases.map(fixture=>verdict(validate,fixture));
  assert.deepEqual(browser,desktop);
  assert.deepEqual(browser,cases.map(fixture=>fixture.expected==='valid'));
  assert.equal(dynamicEvaluations,0);
  process.stdout.write(JSON.stringify({cases:cases.length,equivalent:true,dynamic_evaluations:dynamicEvaluations}));
}finally{globalThis.Function=originalFunction;}
