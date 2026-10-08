// Actual Chromium window uses the fixed UI, form login, HttpTransport and React reducer.
const {app,BrowserWindow}=require('electron');
const {readFileSync,writeFileSync}=require('node:fs');
const {join}=require('node:path');
const input=JSON.parse(readFileSync(0,'utf8'));
let window;let evalViolations=0;let exitCode=0;
function until(operation,timeout=20000){
  return new Promise(async(resolve,reject)=>{
    const deadline=Date.now()+timeout;
    try{while(Date.now()<deadline){if(await operation()){resolve();return;}await new Promise(resolve=>setTimeout(resolve,50));}
      reject(new Error('Real Web UI did not reach the expected state'));}
    catch(error){reject(error);}
  });
}
app.whenReady().then(async()=>{
  try{
    window=new BrowserWindow({show:false,width:1240,height:830,webPreferences:{contextIsolation:true,sandbox:true,
      nodeIntegration:false,webSecurity:true,partition:'http-test-'+Date.now()}});
    window.webContents.on('console-message',details=>{if(String(details.message).includes('unsafe-eval'))evalViolations++;});
    window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    await window.loadURL(input.origin);
    if(!await window.webContents.executeJavaScript('!!document.querySelector("input[type=password]")'))throw new Error('One-time login form missing');
    const navigated=new Promise(resolve=>window.webContents.once('did-finish-load',resolve));
    await window.webContents.executeJavaScript('document.querySelector("input[type=password]").value='+JSON.stringify(input.credential)+';document.querySelector("form").requestSubmit()');
    await navigated;
    await until(()=>window.webContents.executeJavaScript('!!document.querySelector("button.project-link")'));
    await window.webContents.executeJavaScript('document.querySelector("button.project-link").click()');
    await until(()=>window.webContents.executeJavaScript('!!document.querySelector("textarea[aria-label=任务输入]")&&!document.querySelector("textarea[aria-label=任务输入]").disabled'));
    await window.webContents.executeJavaScript('const input=document.querySelector("textarea");Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(input,"Read the value.");input.dispatchEvent(new Event("input",{bubbles:true}))');
    await until(()=>window.webContents.executeJavaScript('!document.querySelector(".composer button").disabled'));
    await window.webContents.executeJavaScript('document.querySelector(".composer button").click()');
    await until(()=>window.webContents.executeJavaScript('document.querySelector("[data-testid=turn-outcome]")?.textContent==="completed"'));
    const report=await window.webContents.executeJavaScript('({rendered_result:document.querySelector(".latest-message")?.textContent.includes("The value is B."),no_raw_node:typeof window.require==="undefined",no_desktop_bridge:typeof window.forgeDesktop==="undefined",errors:document.querySelectorAll("[role=alert]").length})');
    if(!report.rendered_result||!report.no_raw_node||!report.no_desktop_bridge||report.errors||evalViolations)throw new Error('Actual Web UI result or CSP checks failed');
    await window.webContents.executeJavaScript('document.querySelector("[data-testid=turn-outcome]").scrollIntoView({block:"center"});new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    writeFileSync(join(input.directory,'http-browser.png'),(await window.webContents.capturePage()).toPNG());
    writeFileSync(join(input.directory,'http-browser-report.json'),JSON.stringify({status:'pass',...report,csp_eval_violations:evalViolations,eligible_for_native_pass:false,model_origin:'scripted'}));
    process.stdout.write(JSON.stringify({...report,csp_eval_violations:evalViolations,eligible_for_native_pass:false,model_origin:'scripted'})+'\n');
  }catch(error){
    const body=window&&!window.isDestroyed()?await window.webContents.executeJavaScript('document.body.innerText.slice(0,2000)').catch(()=>null):null;
    writeFileSync(join(input.directory,'http-browser-report.json'),JSON.stringify({status:'fail',reason:error.message,body,eligible_for_native_pass:false}));
    process.stderr.write(error.message+'\n');exitCode=1;
  }
  finally{if(window&&!window.isDestroyed())window.destroy();app.exit(exitCode);}
});
