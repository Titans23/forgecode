export * from './core.js';
import {createValidators} from './core.js';
import {validators} from './generated/browser_validators.js';
export const {validate,validateRequest,validateEvent}=createValidators((name,value)=>{
  const validator=(validators as Record<string,any>)[name];
  return validator(value)?null:validator.errors?.[0]??{keyword:'validation'};
});
