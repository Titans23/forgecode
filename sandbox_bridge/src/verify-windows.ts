/** Windows verifier uses the same actual SRT dispatcher and synthetic canaries. */
import { verifyNative, printNative, failedNative } from './verify-native.js';
verifyNative('win32').then(printNative).catch(failedNative);
