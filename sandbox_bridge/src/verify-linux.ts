/** The native launcher verifies the complete installed inventory before this entry. */
import { verifyNative, printNative, failedNative } from './verify-native.js';
verifyNative('linux').then(printNative).catch(failedNative);
