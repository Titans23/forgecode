import React from 'react';

const paths = {
  plus: 'M12 5v14M5 12h14',
  folder: 'M3 7V6a2 2 0 0 1 2-2h4l3 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z',
  chat: 'M20 14a4 4 0 0 1-4 4H8l-4 3V8a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4Z',
  activity: 'M3 12h4l3-7 4 14 3-7h4',
  layers: 'm12 3 9 5-9 5-9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',
  issue: 'M12 8v5m0 3h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  shield: 'M12 3c2 2 5 3 8 3v6c0 4-3 7-8 9-5-2-8-5-8-9V6c3 0 6-1 8-3Z',
  book: 'M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14',
  link: 'M10 13a5 5 0 0 0 7 .1l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7-.1l-3 3a5 5 0 0 0 7 7l2-2',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  send: 'M12 19V5m-6 6 6-6 6 6',
  code: 'm8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16',
  sidebar: 'M9 4v16M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z',
  compose: 'M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6M16 4a2.1 2.1 0 0 1 3 3l-9 9-4 1 1-4Z',
  refresh: 'M20 4v5h-5M4 20v-5h5M20 9a8 8 0 0 0-13.5-5.5L4 6M4 15a8 8 0 0 0 13.5 5.5L20 18',
  check: 'm5 12 4 4L19 6',
  lock: 'M7 10V7a5 5 0 0 1 10 0v3M6 10h12a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Zm6 4v3',
  unlock: 'M7 10V7a5 5 0 0 1 9-3M6 10h12a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Zm6 4v3',
  trash: 'M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6m4-6v6',
  inbox: 'M4 13 7 4h10l3 9v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-6Zm0 0h5l1 3h4l1-3h5',
  file: 'M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8Zm0 0v5h5M9 13h6m-6 4h6',
} as const;

export function Icon({name, size = 18}: {name: keyof typeof paths; size?: number}) {
  return <svg className="ui-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={paths[name]}/></svg>;
}
