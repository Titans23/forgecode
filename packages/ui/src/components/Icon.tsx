import React from 'react';

const paths = {
  plus: 'M12 5v14M5 12h14',
  folder: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
  chat: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  layers: 'm12 3 9 5-9 5-9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',
  flag: 'M5 21V4m0 0c5-4 9 4 14 0v10c-5 4-9-4-14 0',
  settings: 'M4 7h16M4 17h16M8 4v6m8 4v6',
  shield: 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z',
  help: 'M9.1 9a3 3 0 1 1 5.8 1c-1 1-2.9 1.5-2.9 3m0 4h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  send: 'M12 19V5m-6 6 6-6 6 6',
  code: 'm8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18',
} as const;

export function Icon({name, size = 18}: {name: keyof typeof paths; size?: number}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={paths[name]}/></svg>;
}
