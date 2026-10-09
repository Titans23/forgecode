/** Development-only geometry checks against the real, populated renderer. */
import { nativeTheme, type BrowserWindow } from 'electron';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export async function probeLayout(window: BrowserWindow, output: string, page: string, anchor?: string) {
  const checks: Array<{id: string; status: string; details: unknown}> = [];
  const theme = nativeTheme.themeSource, zoom = window.webContents.getZoomFactor();
  try {
    for (const color of ['light', 'dark'] as const) {
      nativeTheme.themeSource = color;
      for (const scale of color === 'light' ? [1, 1.5, 2] : [1, 2]) {
        window.webContents.setZoomFactor(scale);
        await window.webContents.executeJavaScript(`document.fonts.ready.then(()=>{scrollTo(0,0);${anchor ? `document.querySelector(${JSON.stringify(anchor)})?.scrollIntoView({block:'start'});` : ''}})`);
        await new Promise(resolve => setTimeout(resolve, 180));
        const issues: string[] = await window.webContents.executeJavaScript(`(()=>{
          const issues=[];
          if(document.documentElement.scrollWidth>innerWidth+1) issues.push('Page scrolls horizontally');
          const overlay=navigator.windowControlsOverlay;
          if(overlay?.visible){
            const safe=overlay.getTitlebarAreaRect();
            for(const e of document.querySelectorAll('.header-tools > *,.header-heading,.header-open-project')){
              if(!e.checkVisibility())continue;
              const r=e.getBoundingClientRect();
              if(r.left<safe.x-1||r.right>safe.right+1)issues.push('Header overlaps native window controls');
            }
          }
          const controls=Array.from(document.querySelectorAll('.page-header button,main button,main select,main input:not([type=checkbox]):not([type=radio]),main textarea'))
            .filter(e=>e.checkVisibility()&&!e.closest('.virtual-list'))
            .map(e=>({e,r:e.getBoundingClientRect(),name:(e.getAttribute('aria-label')||e.textContent||e.closest('label')?.textContent||e.tagName).trim().slice(0,50)}));
          for(const {e,r,name} of controls){
            if(r.left< -1||r.right>innerWidth+1)issues.push('Outside viewport: '+name);
            if(e.tagName==='BUTTON'&&e.scrollWidth>e.clientWidth+2)issues.push('Clipped button: '+name);
          }
          for(let i=0;i<controls.length;i++)for(let j=i+1;j<controls.length;j++){
            const a=controls[i],b=controls[j];
            if(a.e.closest('.page-header')!==b.e.closest('.page-header'))continue;
            const x=Math.min(a.r.right,b.r.right)-Math.max(a.r.left,b.r.left);
            const y=Math.min(a.r.bottom,b.r.bottom)-Math.max(a.r.top,b.r.top);
            if((x>1&&y> -6)||(y>1&&x> -6))issues.push('Controls touch: '+a.name+' / '+b.name);
          }
          if(Array.from(document.querySelectorAll('.virtual-list')).some(e=>e.querySelectorAll('[data-virtual-row]').length>20))issues.push('Unbounded list DOM');
          return issues.slice(0,20);
        })()`);
        checks.push({id:`layout-${page}-${color}-${Math.round(scale*100)}`,status:issues.length?'fail':'pass',details:issues});
        if (scale === 1 || color === 'dark' && scale === 2) {
          await writeFile(join(output, `layout-${page}-${color}-${Math.round(scale*100)}.png`), (await window.webContents.capturePage()).toPNG());
        }
      }
    }
  } finally {
    window.webContents.setZoomFactor(zoom);
    nativeTheme.themeSource = theme;
    await window.webContents.executeJavaScript('scrollTo(0,0)');
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  return checks;
}
