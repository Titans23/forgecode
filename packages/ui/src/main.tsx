import React from 'react';
import { createRoot } from 'react-dom/client';
import { DesktopTransport, type DesktopOperations } from './transport';
import { App } from './App';
import './style.css';

const webEntry = window.location.protocol === 'http:' && window.location.hostname === '127.0.0.1';
const transport: DesktopOperations = webEntry
  ? new (await import('./http_transport')).HttpTransport()
  : new DesktopTransport();
createRoot(document.getElementById('root')!).render(<App transport={transport} webEntry={webEntry}/>);
